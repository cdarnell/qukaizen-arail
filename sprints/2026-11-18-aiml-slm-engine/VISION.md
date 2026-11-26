# Vision: the house model as ARAIL's engine, opt-in first, default only on evidence

**Verdict: PROCEED, with a sharper frame than the request.** The durable
deliverable is the **Buddy comparison harness**. It is model-agnostic, it runs
today at $0 on models already on this Mac, and it decides the next three model
questions (the mint, stock Qwen3, the held Gemma floor). The opt-in entry is
a small, dormant slot. The pin that fills it is empty until the mint delivers.
This sprint does not change the default.

**Date:** 2026-11-18
**Product:** arail
**Wedge size:** one sprint
**Inputs read:** `SPRINT.md` (this folder); repo and workspace `CLAUDE.md`;
`~/ProJects/programs/2026-10-21-nucleus-domain-slm/BRIEF.md` (§1 naming, §5
recipe, §6 cut rule, §8 S6); the S4 mint status note (data, dated through
2026-11-14); `sprints/2026-10-20-buddy-always-on/{VISION,HARNESS_ALIGNMENT,SPRINT}.md`;
`sprints/2026-10-20-boot-model-autoresolve/SPRINT.md`;
`src/arail/model_resolve.py`; `src/arail/chat/models_catalog.yaml`;
`tests/eval/` (the retrieval A/B harness); `tests/buddy_conformance.py`;
`ollama list` on this Mac.
**Interview:** none live. The owner's words came through the coordinator.
The open points are in §8. Each has a working assumption.

---

## 0. The answer in brief

| Question | Answer |
|---|---|
| User | (1) The owner, developing ARAIL on his M5 Max. He wants the house model under Buddy and chat while he builds. (2) The blueprint user on a clean 16 GB machine. Their experience must be byte-for-byte unchanged. |
| Problem | ARAIL's remaining Buddy and harness work was designed around "a nucleus SLM, about 1B, always on". It is being built and tuned against a 1B Llama placeholder. Nobody can say whether a different brain is better for Buddy, because no instrument measures it. |
| Win | The harness gives a trustworthy verdict on this Mac today, including its own sanity checks. The candidate slot does nothing until it is pinned. Once pinned, it takes one action to switch to it and one action to switch back. |
| Wedge | Three local arms at $0: `llama-ai-eng` (the default), `qwen3:1.7b` (stock), and `qkz-aiml-qwen3-1.7b:control-merge` (the untrained stand-in). Same pre-registered Buddy tasks for each, sent through Buddy's real brain path. Plus a dormant, digest-pinned catalog entry. |
| Disconfirming | For the model: fabricated citations, a domain gain no bigger than noise, `<think>` leaks, or no gain over stock Qwen3. For the harness: an A/A run that shows a "difference", a sensitivity arm it cannot detect, or a served model that is not the one it claims to grade. |
| Displacement | The owner's attention to the mint's open decisions, which are the real critical path. Also the ARAIL `AERO_*` → `QUEUELLM_*` migration sprint and the ARAIL 2.0 provisioning debt. |

---

## 1. User

**Primary: the owner, as ARAIL's developer.** He works on his M5 Max (36 GB)
and runs ARAIL `maximus`. Slot A, the fast resident brain, auto-resolves to
`llama-ai-eng` at boot (`model_resolve.py:397-410`: the shipped default ranks
first whenever it is installed). He talks to Buddy at the desk and from the
phone (both use `brain.chat`). He is about to receive a domain model that his
own program minted: `qkz-aiml-qwen3-1.7b`, a Qwen3 1.7B student, merged GGUF,
served by Ollama 0.40.11. He wants it to be "ARAIL's main LLM engine" for the
rest of development.

**Protected: the blueprint user on a clean machine.** They run `./arailctl
setup` on a 16 GB laptop, pick `minimalist`, and get `llama-ai-eng` from
`llama3.2:1b` plus a Modelfile, with no uploaded artifact. They have never
heard of Nucleus. For them, this sprint must change **nothing**: no new
download, no new network call, no new banner they can act on, no change to
which model boots. The best they should ever see is an `optional` catalog row
that plainly says "not installed".

**Not served by this sprint:** friends-and-family installs, the VM behind
qukaizen.com (BRIEF §8 S6 is a separate serving sprint), and QueueLLM's deep
slot B.

## 2. Problem

He named a solution: "use this model for ARAIL's main LLM engine." The
problems under it:

1. **Development tunes to the wrong brain.** Buddy's prompts, chip rendering,
   cite-or-abstain behaviour and template fallbacks are being built and
   judged on Llama 3.2 1B. If the engine changes later, that tuning has to be
   redone, and some of it may be wrong for the new model. (One example: Qwen3
   is a hybrid thinking model. The mint's own notes say a native import
   emitted `<think></think>` under `think:false`.)
2. **No instrument says whether a brain is better for Buddy.** 3D.EVAL grades
   the *model* against Worlds (the cut). Nothing grades *Buddy on a given
   brain*: the same tasks through the same agent path, side by side. The
   Buddy quality bar from the always-on sprint (20 QA questions, 8 pressure
   cases) was a one-off pass bar, not a comparison.
3. **The model he wants does not exist yet, and when it arrives it will be a
   release candidate.** The mint is stopped on owner decisions: the teacher
   after the refusals, R-B″2′, a second smoke, and READINESS 7/8/11/12. Its
   first output will be unsealed (suite v3 cannot PASS) and its reasoning
   quality unmeasured. Wiring it in as *the* engine on arrival would mean
   trusting an unmeasured RC with every chat turn.

**Sharpened from the vague part.** "Complete the remaining development" is
not concrete. The working reading is: the Buddy and harness roadmap
(always-on S2 eval door, the QA-SME World, the residency policy) should be
developed against the brain it will ship with, and switching brains should be
a measured decision, not a guess. If he meant something narrower, such as
"I need a better coder in chat today", then the cheaper move is in §4.4.
Open point O1.

## 3. Win condition

This sprint wins only if **all** of the following hold, shown by QA on this
Mac.

| # | Win | Measured by | Threshold |
|---|---|---|---|
| W1 | Clean-machine behaviour is unchanged | Setup and boot on a fresh data root, before and after this branch: the slot A resolution, the `arailctl status` JSON (`arail.status/v2`), and the outbound connections | Slot A = `llama-ai-eng` in both runs. Status output identical except for the new catalog row. Zero added non-loopback connections. |
| W2 | The entry is honest while the pin is empty | The catalog row and the model picker with the pin empty, or with a digest that does not match | Says plainly "not installed" (or "installed, but not the pinned build"). Never selected by auto-resolve. Boot does not fail. |
| W3 | The tag alone never passes as the mint | The present `qkz-aiml-qwen3-1.7b:control-merge` tag (same name, wrong digest) | Shown as "not the pinned build". The digest is checked against what Ollama actually serves, not only against the tag string. |
| W4 | Switching is one action each way | Pin the candidate to slot A (the existing `POST /api/models/pin` path), ask Buddy a question, then clear the pin | Buddy answers through the candidate, and the trace records the served model id and digest. Clearing restores `llama-ai-eng` with no restart. |
| W5 | The harness produces a full report | A run over the three local arms (§4.1) | A side-by-side report. **Every number has its meaning and its threshold beside it.** Each arm's served-model digest is recorded. $0 spent, no downloads, wall time ≤ 45 min on this Mac. |
| W6 | The harness passes its own checks in the same run | The A/A, near-A/A and sensitivity checks in §5.2 | All three behave as predicted. If any fails, the report says "harness not trustworthy" and prints **no** promotion verdict. |

**Explicit non-wins.** Even if the stand-in scores well, the report never
says "qkz-aiml is better". The control-merge arm is untrained. Its row is
labelled so, and it exists only to prove the plumbing.

## 4. Wedge

### 4.1 The harness, proved today

- **Arms, all local, no download:** `llama-ai-eng:latest` (default, id
  `fc4a69902baa`), `qwen3:1.7b` (stock base, `8f68893c685c`), and
  `qkz-aiml-qwen3-1.7b:control-merge` (untrained zero-adapter control,
  `078a94854684`, labelled as a stand-in). Later the real mint replaces the
  stand-in, and the arms stay the same.
- **Tasks:** a pre-registered Buddy task set, written and committed before
  any arm's output is read. It should cover the jobs Buddy actually does:
  grounded answers with citation chips from a sealed World, an honest "not in
  the World", lab-state questions, pressure and near-miss refusals, and a
  small AI/ML domain subset (the reason to want this model at all). Size and
  sourcing are architect decisions. Note A3 gives a floor.
- **Path:** the tasks go through Buddy's real brain path (`brain.chat` and
  the desk agent's prompt assembly), not raw Ollama. Otherwise the harness
  measures the model, not Buddy.
- **Grading by code first.** Chip bytes are checked against the sealed
  `terms.json`. Abstains, refusals and format (no `<think>`, no template
  break) are checked by code. Correctness that needs judgement is out of
  scope at $0, unless the architect finds a local judge that taught no arm
  (O4).
- **Report:** three columns plus deltas. Each delta shows its noise floor
  and states plainly whether it clears it. Reps ≥ 3.

### 4.2 The dormant entry

- One `optional` catalog row for `qkz-aiml-qwen3-1.7b`. Its identity is a
  pinned **Ollama tag plus content digest**, filled in only when the mint
  delivers. Until then the digest field is empty and the row says "not
  installed".
- **No bare path into `qukaizen-nucleus`.** The pin comes from a recorded
  source: the mint's release record at a nucleus commit, plus the digest.
- The row is never `recommended` or `flagship`, because those tiers rank
  ahead of unlisted models in auto-resolve (`model_resolve.py:404-406`).
- Disclosure follows the base's licence (Qwen3, Apache-2.0). See Note A6
  on the "hide the base" rule.

### 4.3 What the wedge is not

It does not change the default. It does not change setup. It does not
rename anything on the frozen `aerollm` surface. It does not touch the Gemma
G1 gate. It does not wire QueueLLM in. It does not call 3D.EVAL or nucleus
code at runtime. It spends nothing and downloads nothing.

### 4.4 The cheaper question hiding inside this one

`qwen3:1.7b` (stock) is already installed. The first harness run answers a
question the owner can act on **today, without the mint**: is stock Qwen3
1.7B a better Buddy brain than Llama 3.2 1B? If it is, he can pin it now and
build against the Qwen3 family's behaviour (thinking mode, chat template)
while the mint is stuck. If it is not, that is an early warning: a 1.7B
Qwen3 student starts from a base that Buddy does not favour. Either way, the
stock arm is the baseline the mint must beat (rule R4 below).

## 5. Disconfirming evidence (pre-committed)

### 5.1 About the model: "do not promote"

Any one of these is enough:

1. **Any fabricated citation chip** from the candidate across all reps, or
   more fabricated claims than the default. Cite-or-abstain is Buddy's core
   law, and a confident wrong citation is the trust failure we cannot ship.
2. **The AI/ML domain gain does not clear the noise floor** against the
   default. The only reason to switch is domain expertise. Without it, the
   switch buys a bigger model and a new disclosure, and nothing else.
3. **The candidate does not beat stock `qwen3:1.7b`** on the domain subset.
   In that case the distillation added nothing for Buddy. The honest move is
   to pin stock Qwen3 and feed the finding back to the mint.
4. **Any `<think>` leak or chat-template break** reaches a Buddy reply.
5. **Pressure and refusal behaviour is worse** than the default.
6. **The owner, using his opt-in pin, switches back to `llama-ai-eng` on his
   own within two weeks.** Usage is evidence too. The pin flips are in the
   trace, so this can be seen without asking.

### 5.2 About the harness: "this instrument cannot be trusted"

These checks run in **every** harness run. If any fails, no verdict is
printed.

1. **A/A:** `llama-ai-eng` against itself. Any delta beyond the printed noise
   floor means the harness invents differences.
2. **Near-A/A:** `qwen3:1.7b` against `control-merge`, which are the same
   weights through the merge route. The mint's own measurement put them at
   55.58 % vs 55.84 % on its suite, though not byte-identical. A
   "significant" difference here means the harness is measuring noise or
   route artifacts.
3. **Sensitivity:** a deliberately handicapped arm must lose clearly. For
   example, the default with retrieval or the World context withheld, or the
   smallest local model. If the harness cannot see a known gap, it cannot
   see a real one.
4. **Served-model assertion:** the digest Ollama actually served for each
   arm matches the declared arm. Grading the wrong model is the classic
   failure (BRIEF §6.1).
5. **Re-run stability:** two runs on the same commit produce the same
   pass/fail on every code-graded item, at fixed seed and temperature. The
   architect states what "same" means if Ollama is not deterministic.

### 5.3 About the plan

If the mint has not delivered an artifact by **2027-01-31**, the dormant
entry stays dormant and this decision is revisited. The pre-committed
default is to pin whichever local brain won the harness (possibly stock
Qwen3) and stop waiting.

## 6. Promotion rule: first draft, a PROPOSAL for the owner

This is not a decision. The architect refines the numbers and the owner
approves them. **There are two promotions with two different bars.** This is
the most important line in this file.

**P1: the owner's own engine (a pin on his machine).** He can do this the day
the mint delivers, with no rule at all. It is his machine and his opt-in. The
harness report is advice. Rules R1, R2 and R5 are recommended before he
relies on it for Buddy work, because a fabricating brain will mislead his
own development.

**P2: the blueprint default (`SHIPPED_DEFAULT`, setup's install path).**
Every rule below must hold:

| # | Rule (proposal) | Why this threshold |
|---|---|---|
| R1 | Zero fabricated chips from the candidate across all reps. Fabricated claims ≤ the default's. | Cite-or-abstain is Buddy's law. One fabricated chip is a trust breach on someone else's machine. Zero is the only bar that matches the law. |
| R2 | Overall Buddy task pass rate is **non-inferior** to the default: the candidate is not lower by more than the noise floor. | A default must not regress the everyday lab for everyone in exchange for one domain. |
| R3 | The AI/ML domain subset is ≥ default **+10 points**, and the gain clears the noise floor. | Same size as the BRIEF §6 "beats RAG by +10" margin. That is big enough to be real at n≈30–50 and to justify a larger model and a new disclosure. |
| R4 | The domain subset is above stock `qwen3:1.7b` by more than the noise floor. | Otherwise ship stock Qwen3 (simpler supply chain, upstream-maintained) or nothing. The house model has to earn its name. |
| R5 | Zero `<think>` leaks and zero template breaks. | A format failure is visible on every turn, and the cost is all on the user's side. |
| R6 | Refusal and pressure pass rate ≥ the default's. | ARAIL runs on other people's machines (the arail gating says security is 20 % of QA). |
| R7 | p50 time-to-first-token ≤ 1.5× the default, and resident memory fits the 16 GB `minimalist` floor with the embedder co-resident. | 1.7B vs 1B costs roughly 1.3–1.5× naturally. More than that, or thinking-mode token bloat, makes the lab feel broken on the smallest supported machine. |
| R8 | The candidate is a **sealed release** (3D.EVAL cut PASS imported into the seal), not an RC. | The default ships to strangers. An unsealed RC fails the program's own rule (BRIEF §6: the seal refuses anything but PASS). This keeps the first RC out of P2 by construction. |
| R9 | It installs on a clean machine from a published, licence-bundled artifact with a pinned digest. The install path never touches a sibling repo. | Today the default needs no uploaded artifact. A new default must keep "works on a clean machine" true. |
| R10 | The harness self-checks (§5.2) passed **in the same run** as the verdict. | A verdict from an untrusted instrument is no verdict. |

**Which promotion this sprint enables:** P1 only, and only once the pin is
filled. P2 cannot be met by the first mint (R8), which is correct.

## 7. Displacement

Saying yes costs these, in order of weight:

1. **The owner's attention on the mint itself.** This is the real critical
   path. The model does not exist because of open owner decisions in the
   nucleus session, not because ARAIL lacks a slot. This sprint does not move
   a single one of them. The risk is that ARAIL work feels like progress on
   "the house model" while the mint sits still. Recommendation to the parent
   session: put the mint's waiting list in front of the owner beside this
   sprint's result.
2. **The ARAIL `AERO_*` → `QUEUELLM_*` integration sprint.** It is needed
   before QueueLLM 2.0.0 drops the aliases, and it has a deadline this sprint
   does not.
3. **The ARAIL 2.0 provisioning debt.** #176 and #181 were never applied to
   the real lab, and `--all-instances` reaches 2 of 6 roots.
4. **Buddy always-on S2 (the eval door).** Partly *advanced*, not displaced,
   if the architect builds the harness so S2's 3D.EVAL-driven door can
   reuse its task format (Note A5). If the two drift into two harnesses,
   this becomes pure displacement.

## 8. Open points for the owner (each with a working assumption)

| # | Question | Working assumption |
|---|---|---|
| O1 | What is "the remaining development" concretely? | The Buddy and harness roadmap, built against the brain it will ship with (§2). |
| O2 | Do you accept the two-promotion split (P1 is yours today, P2 needs a seal)? | Yes. Otherwise the first RC could never be used, or would be shipped unmeasured. |
| O3 | If stock `qwen3:1.7b` beats `llama-ai-eng` in the first run, do you pin it now? | Offer it in the report. Do not act without your word. |
| O4 | Is a local LLM judge acceptable for answers that need judgement, given that no arm may be judged by its own teacher? | No judge in this sprint. Code-graded items only. |
| O5 | Is 2027-01-31 the right revisit date if the mint has not delivered? | Yes. |

## 9. Notes for the architect (flags, not design)

- **A1 Auto-resolve can promote by accident.** If the default is absent and
  the candidate is installed, `_resolve_a` picks the candidate as `auto`.
  This is not a clean-machine risk (setup installs `llama-ai-eng`), but on a
  machine where `llama-ai-eng` was removed, an opt-in model becomes the
  brain. Decide whether the candidate must be pin-only. Never give it the
  `recommended` or `flagship` tier.
- **A2 Tag vs digest.** The `control-merge` tag already sits under the
  candidate's name on this Mac. Ollama's model `ID` is a manifest digest, not
  the GGUF blob sha256. Decide which digest is pinned. Make W3 test the
  served model, not the tag string. The mint's notes care about
  served-blob = local digest.
- **A3 Task-set size vs noise.** At n=20 one item is 5 points, so a +10 rule
  is two items. Size the set and the reps so the R3 margin sits clearly above
  the A/A noise floor. Freeze it (by hash) before reading any arm.
- **A4 Thinking mode.** Decide how the harness and the entry set `think` for
  Qwen3, and count a leak as a format failure (R5). Stock and merged arms
  must use identical settings.
- **A5 One harness, not two.** Align the task and row format with Buddy S2's
  planned eval door and with 3D.EVAL's ollama target (`describe()` includes
  `served_model`). Look at `tests/eval/test_retrieval_ab*.py` for the
  existing in-repo A/B pattern (schema-versioned `results.json`, a verdict
  line, auto-skip without Ollama).
- **A6 Disclosure conflict.** Repo `CLAUDE.md` says to hide the base for the
  Apache-2.0 Qwen deep lineage (`ai-engineer`). The program's naming standard
  puts the student in the public name (`qkz-aiml-qwen3-1.7b`). These
  conflict. Surface it; do not resolve it silently. Apache-2.0 attribution
  (NOTICE, licence text) is needed either way.
- **A7 Trace fields.** W4 and disconfirming #6 need the served model id and
  digest on every Buddy turn, plus pin flips, in the existing trace. Check
  what `brain.chat` already records before adding anything.
- **A8 Frozen surfaces.** The candidate lives on the Ollama slot A path. Do
  not route it through the `aerollm` backend, and do not touch `AERO_*`
  names, the Gemma `ARAIL_DEFAULT_GEMMA` gate or `setup.sh`'s default
  install.
- **A9 Clean-machine proof.** W1 needs a real before/after on a fresh data
  root, not only unit tests. The arail QA split puts 30 % on setup.

## 10. Recommended next step

**Proceed to `/architect` (design mode)** with this file as the spec. Scope:
the harness with its self-checks, the dormant digest-pinned entry, and the
promotion rule carried as a proposal for the owner to approve. The default
stays unchanged.
