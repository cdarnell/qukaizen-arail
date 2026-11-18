# Buddy brain A/B harness, and the opt-in house model slot

Sprint `2026-11-18-aiml-slm-engine`. Two things live here: an **instrument** that compares two local
models as Buddy's brain, and a **dormant slot** for the Nucleus-minted `qkz-aiml-qwen3-1.7b`.
The default model does not change (`llama-ai-eng`), setup is untouched, and nothing is downloaded.

## The harness

`python -m arail.brain_bench` sends the same frozen Buddy tasks through ARAIL's real brain path
(`buddy_host.brain`: registry `fast` profile, `ModelRouter`, `OllamaNativeBackend`, Buddy's persona,
card format and answer grammar) for a baseline tag and a candidate tag, grades every reply **in code**,
and writes a side-by-side report. It never calls a paid API, never calls `/api/pull` or `/api/create`,
only talks to loopback Ollama, and runs the arms in a child process with a scrubbed environment and a
temporary `LAB_ROOT` (your `.env`, `secrets.env`, lab, PKB and `model_defaults.yaml` are never read or
written). It restores Ollama residency when it finishes.

```bash
PYTHONPATH=src python -m arail.brain_bench run \
    --baseline llama-ai-eng:latest \
    --candidate qkz-aiml-qwen3-1.7b:control-merge --candidate-label "UNTRAINED stand-in" \
    --reference qwen3:1.7b \
    --near-aa qwen3:1.7b,qkz-aiml-qwen3-1.7b:control-merge
# once the model is released and its pin record is filled:  --candidate pin:qkz-aiml-qwen3-1.7b
python -m arail.brain_bench verify-tasks          # hash check only
python -m arail.brain_bench report <results.json>  # re-render report.md
python -m arail.brain_bench run ... --dry-run      # the plan, nothing sent to a model
python -m arail.brain_bench run ... --limit-per-category 2   # smoke run: no verdict
```

Outputs land in `eval/buddy_brain/runs/<UTC time>-<git sha8>/`: `results.json` (schema
`arail.buddy_brain_ab/v1`), `report.md`, and `rows.jsonl` (every call; git-ignored). A run never
overwrites an earlier one. The whole run is capped at `--max-minutes` (default 45).

| Exit | Meaning |
|---|---|
| 0 | complete, no self-check failed (a verdict is printed only if all eight ran and passed on the full set) |
| 3 | complete, a self-check failed: `HARNESS NOT TRUSTWORTHY: NO VERDICT` |
| 4 | refused to start (Ollama not loopback, arm missing, hash mismatch, guard incomplete, estimate over the cap, pending pin) |
| 5 | aborted (time cap); results are marked `"complete": false`, no verdict |
| 2 | usage error |

### Tasks and grading

`eval/buddy_brain/tasks.v1.jsonl` (194 items, frozen by sha256 in `tasks.v1.manifest.json` together with
the seals of the `ai` and `qukaizen` Worlds) comes from `scripts/eval/buddy_brain_tasks.py` (seed
`20261118`): 100 domain, 32 grounding, 32 out-of-World, 30 pressure items. Grading is code only: JSON
valid for the sent schema, no control-token or `<think>` leak, not empty or cut off, first cite equals
the gold card, correct abstain, pressure rules (planted secret, injected instruction, false and true
pause, off-task), and a template usable-sentence check. Lab-state and multi-turn questions, and any LLM
judge, are out of scope.

Every run executes eight self-checks (A/A, near-A/A, a synthetic and a real sensitivity check, served
model and digests unchanged, grammar integrity, rerun determinism, isolation). If any fails there is no
verdict, and the report says so first. Every number in the report is rendered through
`arail/brain_bench/metrics.py`, which defines its meaning, unit, direction and rationale; the thresholds
live in `eval/buddy_brain/rules.v1.json`.

### The promotion rules are a PROPOSAL

`rules.v1.json` has `"status": "proposal"`. P1 (your own pin) is advisory; P2 (the blueprint default)
needs every rule R1-R11. The first release candidate cannot pass R8 (sealed release). The owner decides.

## The opt-in model slot

* `src/arail/chat/model_pins.json` (`arail.model-pins/v1`) holds one record, `qkz-aiml-qwen3-1.7b`, with
  `status: pending` and empty identity fields. It is filled by a reviewed PR when the Nucleus mint
  delivers (Ollama tag as semver, GGUF sha256, serving parameters, release record). Nothing reads a
  sibling repo.
* `python -m arail.model_pins` prints the pin state (exit `0` match, `3` pending, `4` not installed,
  `5` mismatch or cannot verify).
* The identity is the **GGUF weights sha256** Ollama serves for the exact tag (read from `/api/show`),
  plus the template and declared parameters. A tag alone never passes: an untrained control merge under
  any tag name is a mismatch.
* Auto-resolve never picks the family. A pin works only for an exact tag whose weights match; otherwise
  the pin API refuses in plain words, boot falls back to `llama-ai-eng`, and Buddy's brain reports
  `brain_unavailable`. With Ollama down, the family is not honoured unverified.
* Buddy's brain path now sends `think: false` to any model that reports the `thinking` capability
  (Qwen3 otherwise spends the 400-token cap on hidden reasoning and returns an empty reply).
  `llama-ai-eng` requests are unchanged.
* `qkz-aiml-qwen3-1.7b:control-merge` on a dev machine is an **untrained zero-adapter control**. It is
  a plumbing stand-in for the harness only, labelled as such in every report, and must never be pinned.

Files: `src/arail/model_pins.py`, `src/arail/brain_bench/`, `eval/buddy_brain/`,
`tests/test_model_pins.py`, `tests/test_brain_bench_*.py`.
