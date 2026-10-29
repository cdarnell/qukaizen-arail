# Vision: ARAIL deep, honest

**Date:** 2026-10-29
**Product:** arail
**Wedge size:** one sprint (the upper bound if candidate item 2 is in)
**Verdict:** **PROCEED.** The probe, the end of silent deep failures, honest build-script messages and the issue draft ship in every case. Building QueueLLM main (candidate item 2) is in only if a day-1 probe finds a post-main build that answers 5 of 5.
**Pins:** arail `62d5ebdf` (branch `qukaizen/arail-deep-honest`). QueueLLM main `e730d5fe`. Every arail `file:line` below is at `62d5ebdf`.
**Read:** buddy S6 `DAY1.json`, `BUILD_LOG.md`, `ARCHITECTURE.md` (day-1, A1-A20, tech debt), `VISION.md`, `SPRINT.md`; buddy `DEBT.md` D-S6-8, D-S6-10, D-S6-11; the arail code cited below; QueueLLM `CLAUDE.md:49-63` and `docs/self-hosted-runner-setup.md:136-205`; the operator's ARAIL activity logs (event counts and timestamps only).
**Interview:** none live. Operator items are listed separately, each with a default.

## Read first: five things S6 did not know

1. **The Metal toolchain works today.** `xcrun -sdk macosx metal --version` exits 0 on 2026-10-29 (metal 32023.921). It failed on 2026-10-27 (DAY1.json). The Mac has not rebooted since (uptime 7 days). So the mount is intermittent. The cause is not verified. A build made while the toolchain is down can exit 0 and still be broken (QueueLLM `docs/self-hosted-runner-setup.md:150-158`).
2. **A post-main build already exists.** `~/ProJects/queuellm-gptoss-fix-wt/target/release/libqueuellm_api.dylib`, built 2026-10-11 11:13, branch `qukaizen/queuellm-gpt-oss-fix`. Main `e730d5fe` is an ancestor of that branch; the branch adds 52 commits. The dylib's install name is `@rpath/queuellm_api.abi3.so` and it exports `PyInit_queuellm_api`, so it loads as an extension. Its exact source commit is not recorded. It has not been probed.
3. **The binary outside users get has never been probed.** `deep install` fetches bundle v1.1.0 (QueueLLM `2ae56afb`, `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`). Its `.so` sha256 starts `7c77aa85`. The August build S6 probed starts `5e7c8e0c`. They are different binaries. A tarball whose digest matches the pin (`pyproject.toml:282`) is on disk at `~/ProJects/qukaizen-arail/dist/aerollm-bundle/`. Whether outside users exist is not verified.
4. **The bug already hit the operator's lab, for seven weeks, unnoticed.** World instance `ai` logged `Model 'Qwen2.5-7B-Instruct-4bit' failed: RuntimeError: backend mlx-native failed: ... [rms_norm] ...` at `warn` level on 2026-09-09, 09-11 and 09-21, between 02:04 and 03:14 UTC (`lab/instances/ai/data/activity.jsonl`). That line is the registry's, written only when the entry turns unhealthy (`registry/core.py:283-287`). The researcher's own fall-back line never appears in that log. So the caller was most likely the background agents (`agents/deep_policy.py:294-302`). Which agent is not verified. Nothing in the records shows anyone noticed before the S6 architect's probe on 2026-10-27.
5. **The rebuild path has a second defect.** In the operator's venv, `import aerollm_api` resolves to the pip package `aerollm_api/__init__.py` (checked with `find_spec`; nothing imported). `cargo_build` copies its build to the top-level `aerollm_api.abi3.so` (`scripts/build-aerollm.sh:149-153`). The package shadows that file. A shadowed top-level copy from 2026-05-02 is already there. `verify_or_die` checks only that the import works (`:174-180`). So a rebuild that compiled would print "QueueLLM ready" while ARAIL kept loading the June binary. That package's own `.so` was replaced outside pip on 2026-06-28: pip's RECORD says 20 260 240 bytes, the file has 21 894 128, and it reports version `0.1.0-rc.2` under a `0.1.0` dist-info. Its provenance is unknown.

**Corrections to the brief.** `deep_policy.py` is at `src/arail/agents/deep_policy.py`. The Chat tab is **not** silent. A deep error comes back as "Inference failed: <error>", with no fall-back (`portal/app.py:8103-8113` streaming, `:8229-8238` non-streaming). The silent ones are the background agents and the World Curator.

## Verified: each deep consumer when call 2 fails

| Consumer | Code | What the operator sees | Evidence |
|---|---|---|---|
| Chat, deep picked | `portal/app.py:7974`, `:8161`; errors `:8103-8113`, `:8229-8238` | "Inference failed: <raw rms_norm text>". Loud. No fall-back. | Code read. A second Chat turn was not run; inferred from S6's probe through the same backend. |
| Background agents: Buddy's proactive voice, debt advisor, consolidation analyzer | `agents/_builtin_buddy.py:175-188`, `_builtin_debt_advisor.py:91-100`, `_builtin_consolidation_analyzer.py:92-101`, all calling `deep_policy.complete_preferring_deep` (`:264-323`) | Fast-model text, unlabelled. One `warn` line when the entry turns unhealthy, none after (`registry/core.py:283-287`). | Code read. Seen 3 times in the `ai` lab. |
| World Curator review | `portal/world_routes.py:125-136` (deep when policy allows), `:902-932`; vendored `dac_world/reconcile.py:213-217` | Each failed judge call is skipped. The pass still records `reviewed_count = min(16, len(terms))` (`:918`) and says "All clear" if there are no flags (`:927`). A false all-clear. | Code read, not run. Latent: the logs show 20 reviews, all of a 2-term test World on 2026-08-28. |
| World Curator grow | `world_routes.py:1016-1080`; `reconcile.py:114-116`, `:140-141`, `:170-171`, `:213-217` | Failed calls are skipped. The bundle is still resealed and swapped (`:1049-1051`). The log reports "evolved via <model>" as a success (`:1072-1077`). | Code read, not run. |
| Researcher | `agents/researcher.py:278-315` | A warn line and a registry fall-back event on every failure. | Code read. Visible. No change. |
| Nucleus (Model Forge) | `nucleus/providers/queuellm.py:104-117` | The error propagates. | Code read. Loud. No change. |
| World forge, brain auto or deep | `world_routes.py:172` | Not verified. | The architect checks it. |

What the status surfaces say at the same moment:

| Surface | What it says | Why it misleads |
|---|---|---|
| `./arailctl deep status` (`build-aerollm.sh:397-428`), run today against the live venv | `aerollm_api` importable, version `0.1.0-rc.2`; "crate .../crates/aerollm-api (missing → release/bundled channel)"; "channel: release" | Import is not generation. The crate exists, renamed `crates/queuellm-api`. The channel is a guess. |
| `deep_policy.explain()` (`:138-161`), shown by the research truth strip (`portal/app.py:4559-4575`) | `ok`, "deep inference available" | It checks the kill switch, the tier and importability only (`:146-157`). No reason code means "installed but failing". |
| Registry health of `tier1-aerollm` | After a failed call: `unhealthy`. After any re-probe: "healthy: runtime resident (warm)". | A re-probe tests residency, not generation (`registry/health.py:132-140`). The Models "Check" button (`portal/models_api.py:57-66`) and every deep warm (`portal/model_warmth.py:111`, `:251`, `:452`) re-probe and erase the failure. So does the opt-in 60 s loop (`health.py:261`; off by default, `portal/app.py:955-962`). The status bar shows no tier1 health at all (`registry/core.py:470-481`). `AeroLLMBackend.health_check` makes the same residency test (`router/backends.py:1892-1893`). |
| `./arailctl deep rebuild` (`arailctl:804` → `build-aerollm.sh build`) | "No QueueLLM sibling repo at ~/ProJects/qukaizen-queuellm" | False. The crate path is pre-rename (`:46`), checked at `:132`, message from `:122-129`. Past that: `-p aerollm-api` (`:138`), `libaerollm_api` (`:141-142`), and the shadowed copy (fact 5). `setup.sh:684` uses `auto` mode, which on main would print "No sibling repo" and fetch the bundle instead (`:445-453`). |

## User

Charlie, the operator. An M5 Max with 36 GB. ARAIL on `LAB_TIER=maximus`. Deep model `Qwen2.5-7B-Instruct-4bit` in `/Users/Shared/models`. World instances such as `ai`.

What his logs show about how he uses deep today:
- Background deep work in the `ai` lab. The three failures were logged at 22:04 to 23:14 his time (EDT). Background deep needs the lab outside its active window (`deep_policy.py:73-76`).
- 20 Curator reviews on 2026-08-28, all of a 2-term test World.
- No deep error from Chat since August.

So today his deep use is mostly background work he does not watch. He trusts the status surfaces, and they told him deep was fine. Next he wants deep from his phone: `/deep`, his S6 steer, cut on day 1.

## Problem

Not "add a probe". The pain: **ARAIL cannot tell him whether its deep model works. When it does not, two consumers hide it.**

- Every status signal tests something other than generation: importable, policy ok, weights resident. The one that records a real failure, registry health, is erased by the next re-probe, the Models "Check" button included.
- The background agents swap in the fast model behind one easy-to-miss line.
- The Curator skips failed judgments and reports a clean pass.
- The one repair route, `deep rebuild`, cannot build QueueLLM main, prints a false reason, and on this Mac would install a binary ARAIL never loads.

The call-2 bug itself is QueueLLM's. ARAIL's part is the silence and the broken repair route.

## Win condition (one pass/fail)

**On this Mac, with the operator's current wheel left exactly as it is, one scripted run ends with 0 silent outcomes.**

The script is the architect's to name (S6's pattern is `scripts/s6-win.sh`). In order:
1. It drives each consumer (Chat, the background agents, Curator review, Curator grow) through at least 5 deep calls, each in a fresh portal process with no probe record, so the call-2 failure lands inside that consumer. It reads every status surface after each, and presses the Models "Check".
2. It runs `./arailctl deep probe`.
3. It starts one more fresh portal process and reads every status surface before any deep call.

A silent outcome is any one of:
1. a fast-model answer shown, stored or counted as deep;
2. a Curator review or grow reported as done ("All clear", "evolved via <deep model>", a `reviewed_count` higher than the judge calls that returned) when any of its deep calls failed;
3. a grow pass that reseals or swaps a bundle after its deep calls failed;
4. a status surface (`deep status`, `explain()` and the truth strip, `tier1-aerollm` health) that says deep is available, ok or healthy after a deep call failed in that process, or while the last probe of the same binary failed;
5. a false line from `scripts/build-aerollm.sh`, in any mode.

**Pass:** 0 silent outcomes, and `deep probe` prints `1/5`, the first error with its call index, the file `import aerollm_api` resolves to, and that file's sha256. That matches DAY1.json. **Fail:** any silent outcome, or a probe result that disagrees with DAY1.json.

The bug is the fixture. The June wheel fails call 2 every time, so the main rows need no fake runtime. Chat is a regression row: it must stay loud.

If the day-1 check puts item 2 in, the same script adds one row. In a copy of the venv, `deep rebuild` builds QueueLLM main, `import aerollm_api` still works, the sha256 of the file it resolves to equals the sha256 of the file just built, and `deep probe` on it prints 5/5.

## Wedge

Yes, there is a smaller slice than the four candidates. The wedge is **one true answer about deep, wherever ARAIL is asked.**

1. **`./arailctl deep probe`** (candidate 3).
   - 5 sequential calls through ARAIL's own `AeroLLMBackend`, in a fresh process, never inside the portal.
   - It prints the pass count, the median latency of the calls that passed, the first error and its call index, the imported file, its sha256 and the module version. Exit 0 only on 5/5.
   - The 5 calls mix a short question with a prompt as long as a real ARAIL chat turn with lab context. The July and August builds fail in chunked prefill, and one short question repeated may miss a partial fix.
   - Explicit only. Never at boot (ARAIL boots quiet).
   - It must not load a second resident copy of the model without saying so. That is fine on 36 GB, not on a 16 GB blueprint Mac.
   - It stores its result keyed by the binary's sha256, so status can read it.
2. **A deep failure is sticky and shown** (candidate 1, narrowed).
   - A failed deep call marks deep as failing for that process, with the error and time. A later successful call in the same process clears it.
   - A failed probe marks that binary as failing until a probe of the same binary passes 5/5. A first call that works in a new process does not clear it: call 1 worked on every build tested.
   - Status says failing while either is true. `explain()` returns a new, additive reason code for it (the architect names it). The truth strip, `deep status` and `tier1-aerollm` health show it. No residency check clears it.
   - The background agents then take the fast path under that visible reason, as they already do for `deferred_now` (`deep_policy.py:280`, `:303`).
3. **Curator passes report what happened** (candidate 1, narrowed). Review and grow count their failed deep calls at ARAIL's call site in `world_routes.py`. A pass with any failed call reports "partial: K of N judged; deep failed: <reason>", never "All clear". A grow pass with failed calls does not reseal or swap. This row is in although the logs show no harm yet. It is the one place where the silence becomes a false claim about a World, and the fix is small.
4. **No false line from the build script.** `deep status` shows the last probe result, or says generation has never been tested. It names whichever crate it finds, old name or new. It says "unknown" for a channel it cannot prove. When item 2 is out, `deep rebuild` and `auto` refuse with the true reason: QueueLLM main has `crates/queuellm-api`, and this ARAIL builds only the pre-rename crate (D-S6-10).
5. **The QueueLLM issue, drafted** (candidate 4). `ISSUE.md` in this folder: S6's text (BUILD_LOG, Architect feedback item 2) plus every day-1 data point. Filed only with the operator's OK.
6. **Conditional: build QueueLLM main** (candidate 2). Only if day 1 passes (next section).

**Cut from the candidates:**
- **The opt-in switch for falling back to fast.** The fall-back is not what failed the operator for seven weeks. The missing signal was. A default-off fall-back makes the agents quieter, not louder. Buddy's future `/deep` already has no fall-back (S6 ARCHITECTURE, F2).
- **New wording for the Chat error.** It is already loud.

## Day-1 check and the cut rule (pre-committed)

This runs before any ARAIL code. Half a day, scratch only. Nothing goes into the live venv. The QueueLLM repo is not touched (`git archive`, scratch `CARGO_TARGET_DIR`). Same method as S6: ARAIL's venv python, `PYTHONPATH` shadowing, `ARAIL_DATA_DIR` in scratch, no portal, the DAY1.json probe settings.

- **D1.** Record `xcrun -sdk macosx metal --version`.
- **D2.** Probe the 2026-10-11 build (fact 2) through a scratch `aerollm_api` shim, so ARAIL's own backend loads it.
- **D3.** Probe bundle v1.1.0 from the local tarball (fact 3). No download.
- **D4.** If D1 exits 0: build main `e730d5fe` (`-p queuellm-api --features extension-module`). Run QueueLLM's preamble check: `strings <artifact> | grep -c downloadComponent` must print 0 (`docs/self-hosted-runner-setup.md:189-205`). A build that fails it is thrown away unprobed and counts as "could not build". Then probe it. Time box for build and probe: 3 hours of wall clock.
- **D5.** Write `DAY1.json` here, with S6's teaching block for every number.

| Day-1 result | Item 2 | Rest of the wedge |
|---|---|---|
| main 5/5, any median | **In.** The real install into the live venv is the operator's (O2). | Ships. |
| main could not be built, and the 10-11 build is 5/5 | **In.** Prove the script against a fixture and the 10-11 artifact in a venv copy. Retry the main build once before the PR. If it still cannot build, the main row is OWED. | Ships. |
| main fails call 2, or main could not be built and the 10-11 build fails call 2 | **Out.** Only the honest refusal ships. | Ships. |

**If the rebuilt wheel still fails call 2:** the sprint does not try to fix it. Wedge rows 1 to 5 all hold on a broken wheel, so they ship. Every data point goes into `ISSUE.md`. The live venv is not touched. Item 2 and Buddy's `/deep` reopen only when a QueueLLM build passes `deep probe` 5/5. For `/deep`, the median must also be at most 120 s (D-S6-8's line).

D3 changes no scope. It sets how loud the issue is. If the bundle fails too, every maximus user who ran `deep install` has a deep slot that answers once per process.

**Revisit date: 2026-11-19.** QueueLLM went MoE-exclusive on 2026-08-05. It lists Qwen2-dense as legacy: "Keep them building, but invest new effort only where it serves the MoE lane" (QueueLLM `CLAUDE.md:49-63`). ARAIL's deep slot runs a dense Qwen2.5-7B. A fix may not come. If there is none by 2026-11-19, this stops being a bug and becomes a product choice for the operator: wait, move ARAIL's deep slot to an MoE model QueueLLM invests in, or take QueueLLM out of the slot. That choice gets its own visionary pass. It is not this sprint.

## Disconfirming evidence (pre-committed)

1. **The probe lies.** `deep probe` says 5/5 on a binary, then a real portal session on the same binary fails a deep call. The probe does not measure what ARAIL does. No "deep works" claim and no `/deep` until the probe reproduces that failure.
2. **The status is in the wrong place.** The June wheel will keep failing. If within 14 days of merge the operator has not seen "deep failing" in ARAIL on his own (he hears it from Claude, or not at all), the surfaces are not where he looks. Stop, and ask him where he looks before adding more status. The witness is one line from him in the ledger: the surface and the date.
3. **Desk deep is not a real need.** In the 30 days after merge he never runs `deep probe`, never picks deep in Chat, never asks for the build path, and does not ask again for `/deep`. Then this was insurance on an unused feature. Do not build `/deep` on ARAIL's deep slot. Revisit the S6 steer with him.
4. **The cheap fix is not cheap.** If the wedge needs an edit to `src/dac_world/`, a new long-running process, or a change to the Buddy contract, stop and come back here. The wedge assumes ARAIL call-site changes only.

## Displacement

A yes takes Claude-side time from:
- the Buddy debt queue: D-S5-13, D-S5-16, D-S5-18, D-S6-7, D-S6-9;
- ARAIL's frozen-name sprint (moving `AERO_*` to `QUEUELLM_*` before QueueLLM 2.0.0 drops the alias). If item 2 is in, it touches the same surface, and its `aerollm_api` alias must be one that sprint can keep;
- 3D.EVAL and the nucleus SLM work, which would produce a brain able to pass Buddy's bake-off. As in S6, this is the real cost;
- QueueLLM's attention: the issue asks an MoE-exclusive team to look at a legacy-lane bug.

It does not touch the phone runs the operator owes (S5 step 1, S6 step 7, one D-S5-13 trial). Buddy S7 still waits on those (S6 VISION). This is an ARAIL sprint, not S7.

Accepted for one sprint. Deep is the one maximus capability that is broken. It failed in his real lab for seven weeks with no sign he saw. And every later deep consumer, `/deep` first, needs a probe it can trust.

## Needs the operator (separate from the build)

| # | Item | Default if he says nothing | Testable without it |
|---|---|---|---|
| O1 | The Metal toolchain. It works today. If it fails again during day 1, the repair is his call. QueueLLM's doc covers a *missing* toolchain (`docs/self-hosted-runner-setup.md:136-205`). This Mac's case, installed but not runnable by `xcrun`, is not covered there. Which step fixes it is not verified. | No repair. D4 counts as "could not build". | Everything except the main build: the whole win, D2, D3, the issue draft. |
| O2 | Anything installed into or removed from `~/ProJects/qukaizen-arail/.venv`: a rebuilt runtime if item 2 is in; the shadowed top-level `aerollm_api.abi3.so` from 2026-05-02. | Nothing is installed or removed. | The win runs on the venv as it is. Item 2's row runs in a venv copy. |
| O3 | Filing the QueueLLM issue in `cdarnell/qukaizen-queuellm`. | Draft only. D-S6-11 stays open. | All of it. |
| O4 | The witness line for disconfirming item 2. | OWED. | Not testable by CI. |
| O5 | The product choice on 2026-11-19 if QueueLLM has no fix. | Revisit then. | n/a |

One policy question, with a working assumption: while deep is failing, the background agents keep running on the fast model under the visible reason. No new switch. If he wants them to stop instead, that is a small policy change for the architect.

## Constraints for the architect

- Never rename `aerollm`, `AERO_*`, `aerollm_api`, `tier1-aerollm`, `AeroLLMBackend` or the `*-aerollm*.sh` scripts. `nucleus/runtime_names.py:29-33` is the in-repo pattern for keeping a frozen name in one place.
- No edit to `src/dac_world/`. It is vendored DDaC code (ADR-0004) with no drift check. Fix at ARAIL's call sites.
- Nothing installed in the live venv. The QueueLLM repo is not touched.
- Any record of a build carries the QueueLLM commit and the artifact sha256, like `THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`.
- If item 2 is in: verification compares the sha256 of the file `import aerollm_api` resolves to with the file just built. Import alone is not verification. `auto` mode must not switch channel silently when it finds the renamed crate.
- Reason codes are additive. `explain()` codes reach the truth strip, and Buddy's future `/deep` sends them to the phone verbatim (S6 ARCHITECTURE, route step 4). Existing codes keep their meaning. "Failing" stays distinct from `deferred_now`.
- No network in the probe or in status. Airgapped mode is unaffected.
- Every number the probe and the win script print carries its meaning, its threshold and why, what out of range means, and where the failure lives. DAY1.json's `teaching` block is the model.
- User-facing text that mentions cost says "token-free".

## Cut

- Buddy `/deep`, `brain.deep` and any contract delta. They reopen per D-S6-8.
- Any QueueLLM code change, the call-2 fix included.
- The opt-in fall-back switch, and new Chat error wording.
- The bundle channel, and a re-pin of v1.1.0.
- The `AERO_*` to `QUEUELLM_*` migration, and every rename.
- Moving the deep slot to another model (the 2026-11-19 question).
- The researcher and nucleus failure paths (already loud).

## Recommended next step

Proceed to `/architect` design with this file as the spec. The architect runs the day-1 check (D1 to D5) first, scratch only, as the S6 architect did. Then they design only the branch the result picks. Item 2 is designed only if it is in.
