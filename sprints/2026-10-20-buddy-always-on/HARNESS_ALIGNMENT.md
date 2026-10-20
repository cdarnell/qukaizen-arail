# Harness alignment (qukaizen.com/harness, read 2026-10-20)

Operator steer: "see https://qukaizen.com/harness and update and align
accordingly." This note maps the sprint onto the page and records where they
differ. The architect's Addendum A carries the design consequences (G13).

## The page, in short

`cdarnell/qukaizen-harness` — "A repository, not a product. Apache-2.0, five
modules, zero product imports." Not on GitHub yet (repo list 2026-10-20). Five
parts: **Registry** (every tool declared once and versioned; a tool not in it
does not exist to the crew), **Model** (an 8–14B resident, never evicted; the
policy is a file, enforced at load), **Context** (DDaC compiles the mounted
World into the wire payload; stable parts first for caching, recent parts
last; nothing pasted by hand), **Guardrails** (output grammar-constrained from
the registry's schemas; everything a crew produces passes the World gate; it
describes, never prescribes), **Loop** (wake, look, decide, act, record a
hashed ledger line, sleep). Buddy on the page: "The lab's resident colleague.
Runs the Autoresearch loop and 3DEval between your sessions."

Standing rules, verbatim: "The resident model is never evicted. Everything
else yields to it." · "Nothing reaches the shelf without passing the gate — a
crew's output included." · "Nothing leaves the machine. Sharing moves rules,
never data." · "Every run leaves a ledger line with a hash. If it isn't in the
ledger, it didn't happen." · "The crew describes. It never prescribes. A flag
waits for a person." · "A correction becomes code. An exception becomes a
rule, once."

## Mapping S1 onto the five parts

| Harness part | S1 piece | Where |
|---|---|---|
| Registry | The frozen action table (one action, `arail.pause_research`) and the closed `propose` enum; later the paperagents policy (`deny`/`ask`, no `allow`) | ARCHITECTURE §6, §5.19; RESEARCH_NOTES §3 |
| Model | ARAIL's `brain.chat` fast brain through `ModelRouter`; residency policy = ARAIL's `deep_policy` + the edge's "fast brain only" rule | ARCHITECTURE §5.6; VISION §2.4 |
| Context | The sealed `qa-practice` World injected as delimited data by a context provider; chips rendered from sealed bytes | ARCHITECTURE §5.20–5.21; VISION §3.6 |
| Guardrails | Grammar-constrained structured output; cite-or-abstain; "describes, never prescribes" = propose → card → human confirm | ARCHITECTURE §6; CLAUDE.md laws 2 and 5 |
| Loop | `trace.record` (append-only, schema-checked) and the approval ledger; every step in Admin lanes (win step 7) | ARCHITECTURE §5.7, §5.10 |

## Where S1 differs from the page (recorded, not silently resolved)

1. **Resident model size.** The page pins an 8–14B resident that is never
   evicted. S1's brain is the 1B `llama-ai-eng` via ARAIL's fast path, per the
   operator's steer ("a nucleus-driven SLM ~1B always on; QueueLLM on demand
   for deep reasoning"). Known difference; the residency *policy* (a file,
   enforced at load) is the part S1 copies. Revisit at the brain bake-off.
2. **"Nothing leaves the machine. Sharing moves rules, never data."** The
   Telegram channel moves bytes off the box. S1 complies with the rule's
   second sentence if off-box messages carry only sealed World rules (chips),
   fixed texts and the operator's own words, never lab data. Proposed as gate
   **G13** for the architect to ratify in Addendum A and for QA to test.
3. **Repository shape.** The page names `qukaizen-harness` as the floor the
   products stand on. S1's harness pieces live in `qukaizen-buddy/buddy/` and
   ARAIL's edge. Working assumption: extraction into `qukaizen-harness` is a
   later sprint, triggered when a second resident (the TEAM crew) needs the
   same registry, guardrails and loop. Open item O8 for the operator.
4. **Buddy's stated job** ("runs the Autoresearch loop and 3DEval between your
   sessions") is S2+ (VISION phasing: eval door and 3D.EVAL target in S2;
   proactive loop later). S1 proves reach, cite, confirm, stop.

## Follow-up outside this sprint

The site page should gain the consented-channel exception (rule 2) once G13
holds, in `qukaizen-site`. Not done here: a public page is outward-facing and
belongs to its own change.
