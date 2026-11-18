# Sprint: aiml-slm-engine

**ID:** 2026-11-18-aiml-slm-engine
**Started:** 2026-11-18
**Product:** arail
**Branch:** `qukaizen/arail-aiml-slm-engine-87c49e`

## Task
Make ARAIL ready to use the Nucleus-minted `qkz-aiml-qwen3-1.7b` model (Qwen3 1.7B student, LoRA merged
into the stock GGUF, served through Ollama pinned at 0.40.11) as its LLM engine, in two steps: an opt-in,
selectable entry now; default only on evidence. Scope: (1) verify in code how ARAIL selects and loads local
models and write it down; (2) add the model as an opt-in entry driven by a pinned identifier (Ollama tag +
content digest) that is filled in when the mint delivers, failing gracefully ("not installed") until then,
with no bare path into any sibling repo; (3) build a Buddy comparison harness (same Buddy tasks, current
default vs a candidate, side-by-side report with meaning and thresholds beside every number) and prove it
now against a locally present model; (4) state a promotion rule as a proposal for the owner; (5) do not
change the default, do not rename anything on the frozen `aerollm` surface, keep setup-on-clean-machine
and security gates green.

## Facts established before think (2026-11-18, verified on this machine)
- No trained adapter exists. The mint (nucleus PR #71, worktree `~/ProJects/nucleus-s3-wt`) is stopped on
  owner decisions; a first smoke adapter on a local teacher is in progress in another session.
- `ollama list` shows `qwen3:1.7b` (stock, id `8f68893c685c`) and `qkz-aiml-qwen3-1.7b:control-merge`
  (id `078a94854684`) already present. The latter is an **untrained zero-adapter control** from the mint's
  serving rehearsal, not a trained model. Ollama is 0.40.11. No download is needed to prove the harness.
- The current default `llama-ai-eng:latest` is installed locally.

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-11-18 | 2026-11-18 | proceed (`1867ce99`) |
| plan | architect (design) | ARCHITECTURE.md | done | 2026-11-18 | 2026-11-18 | complete (`d0922a40`) |
| build | builder | BUILD_LOG.md | done | 2026-11-18 | 2026-11-18 | done (`31ef7a08..e2c47bca`) |
| review | architect (review) | REVIEW.md | BLOCK → fix loop 1 | 2026-11-18 | — | BLOCK (`94a247f3`) |
| test | qa | TEST_REPORT.md | pending | — | — | — |
| ship | — | PR | pending | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-11-18 | Opt-in first, default only on evidence | Coordinator recommendation; owner answered "Let's go!" |

## Skipped phases

| Phase | Reason |
|---|---|

## Notes
- VISION.md commit `1867ce99` carries a `Claude Opus 5.5` co-author trailer: that is the model the visionary subagent ran on. Left as is (accurate).
- Plan findings (ARCHITECTURE.md): (1) same-base-name fallback would serve `:control-merge` for a pin on the real tag — closed by exact-tag + GGUF-digest check; (2) Qwen3 thinks by default on Ollama 0.40.11 and Buddy's path never sends `think`, so replies come back empty — fix is `think:false` only for models reporting the thinking capability. Owner questions O1–O10 are open; none blocks the build.
- Review 1 BLOCK: the build's NOTICE edit (`298d5898`) turned the frozen NOTICE-sha256 test red and BUILD_LOG called it pre-existing. Nine further ASK/INFO findings. Fix loop waits for the in-flight harness run to end (the loop moves/edits harness files), then a fresh evidence run is made on the fixed code.
