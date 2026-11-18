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

Outputs land in `bench/buddy_brain/runs/<UTC time>-<git sha8>/`: `results.json` (schema
`arail.buddy_brain_ab/v1`), `report.md`, and `rows.jsonl` (every call; git-ignored). A run never
overwrites an earlier one. The whole run is capped at `--max-minutes` (default 45).

| Exit | Meaning |
|---|---|
| 0 | complete, no self-check failed, and (full run) a verdict printed. A `--limit-per-category` smoke run may exit 0 without a verdict |
| 3 | complete, a self-check failed or did not run or was inconclusive, so no verdict: `HARNESS NOT TRUSTWORTHY: NO VERDICT` |
| 4 | refused to start (Ollama not loopback, arm missing, hash mismatch, guard incomplete, estimate over the cap, pending pin) |
| 5 | aborted (time cap or interrupt; residency is restored and the temp root removed); results are marked `"complete": false`, no verdict |
| 2 | usage error |

### Quiet lab precondition

Any run that produces evidence needs a **quiet lab**: no portal, no pytest, no `arailctl` command and no other
agent running on the same checkout while it runs. The harness proves its own children wrote only to a
temporary root (SC8a: every write-capable path it resolved is inside the temp root, and a canary plus
`agent_traces.jsonl` landed there). SC8b then compares the real lab roots before and after; if something
else wrote to them, SC8b is **inconclusive** (no verdict, exit 3) rather than blaming the harness.

### Tasks and grading

`bench/buddy_brain/tasks.v1.jsonl` (194 items, frozen by sha256 in `tasks.v1.manifest.json` together with
the seals of the `ai` and `qukaizen` Worlds) comes from `scripts/bench/buddy_brain_tasks.py` (seed
`20261118`): 100 domain, 32 grounding, 32 out-of-World, 30 pressure items. Grading is code only: JSON
valid for the sent schema, no control-token or `<think>` leak, not empty or cut off, first cite equals
the gold card, correct abstain, pressure rules (planted secret, injected instruction, false and true
pause, off-task), and a template usable-sentence check. Lab-state and multi-turn questions, and any LLM
judge, are out of scope.

Every run executes the self-checks SC1-SC8b: A/A, near-A/A (a metric that is flat at 0% or 100% is
"uninformative (zero variance)", not "inside"), a synthetic sensitivity check, **SC4** (on the reference arm, opaque
cite ids `t1`-`t5` over the 86 example-form and short-form items: card content must beat ids-only by 15 pp with the
interval excluding 0, ids-only must sit within 10 pp of the 20% chance rate, and content must reach 35%), served
model and digests unchanged, grammar integrity, rerun determinism, and isolation (SC8a attribution, SC8b quiet lab).
The slug+term "withheld" block is informational only (M9, content dependence). The report also shows, per arm,
where the first cite lands and whether M1 is statistically at chance. If any fails there is no
verdict, and the report says so first. Every number in the report is rendered through
`arail/brain_bench/metrics.py`, which defines its meaning, unit, direction and rationale; the thresholds
live in `bench/buddy_brain/rules.v2.json`, hash-pinned in `rules.pin.json`: a rules file whose sha256 is not
in the pin record is refused, and `results.json` records the real path. `rules.v1.json` is kept unchanged as the
superseded record.

### The promotion rules are a PROPOSAL

`rules.v2.json` has `"status": "proposal"` (v2 measures R1 against the stock reference, R3 against the better of the default and the reference, and adds R12 on first-cite position bias, because the default brain scored at chance in the first full run). P1 (your own pin) is advisory; P2 (the blueprint default)
needs every rule R1-R11. The first release candidate cannot pass R8 (sealed release). The owner decides.

## The opt-in model slot

* `src/arail/chat/model_pins.json` (`arail.model-pins/v1`) holds one record, `qkz-aiml-qwen3-1.7b`, with
  `status: pending` and empty identity fields. It is filled by a reviewed PR when the Nucleus mint
  delivers (Ollama tag as semver, GGUF sha256, serving parameters, release record). Nothing reads a
  sibling repo.
* **Base and licence:** Qwen3-1.7B, Apache-2.0 (also in the pin record's `model` block). ARAIL does not
  redistribute the weights; the Apache-2.0 notice duty falls on whoever distributes them (the Nucleus release),
  so `NOTICE` (byte-pinned by the frozen-surface guard) is deliberately not edited.
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

Files: `src/arail/model_pins.py`, `src/arail/brain_bench/`, `bench/buddy_brain/`,
`tests/test_model_pins.py`, `tests/test_brain_bench_*.py`.
