# Build log: aiml-slm-engine (opt-in house-model slot + Buddy brain A/B harness)

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `34c4c286`
**Started:** 2026-11-18

Test interpreter (main checkout venv, read-only): `PY=/Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python`,
run as `PYTHONPATH=src $PY -m pytest ... -q -p no:cacheprovider` from the worktree.

## Plan

| # | Files | Change | Test | Commit |
|---|---|---|---|---|
| 0 | sprints/.../BUILD_LOG.md | skeleton | - | pending |
| 1 | tests/test_model_resolve_pinned_family.py | characterisation + strict xfails for F1/F2 | T-R6 | pending |
| 2 | src/arail/chat/model_pins.json, src/arail/model_pins.py, tests/test_model_pins.py | pending pin record, loader, verify, CLI | T-P1..P8 | pending |
| 3 | src/arail/model_resolve.py | family pin-only, exact tag, digest-verified | T-R1..R5 | pending |
| 4 | src/arail/portal/models_api.py, src/arail/portal/app.py | refuse unverified family pins; trace pin flips | T-A1..A3 | pending |
| 5 | src/arail/router/{core,backends}.py | think/sampling/trace_attrs pass-through | backend body tests | pending |
| 6 | src/arail/buddy_host/brain.py, portal/host_edge_routes.py | stream_turn; think off; family re-verify | T-B1..B5 | pending |
| 7 | chat/models_catalog.yaml, chat/__init__.py, model_resolve.py, model_specs.py, NOTICE | dormant catalog row, pin_state | T-C1, T-C2 | pending |
| 8 | src/arail/brain_bench/{vendor,compose,grade,stats,metrics} | vendored prompt, grading, stats | units, T-H10, T-H11 | pending |
| 9 | scripts/eval/buddy_brain_tasks.py | deterministic task generator | generator tests | pending |
| 10 | eval/buddy_brain/{tasks.v1.jsonl,manifest,rules.v1.json} | freeze tasks and rules (data only) | T-H1 | pending |
| 11 | src/arail/brain_bench/{arms,run,report,__main__}, .gitignore | runner, isolation, SC1-SC8, report | T-H2..T-H9 | pending |
| 12 | tests/eval/test_buddy_brain_live_ollama.py | live smoke (requires_ollama) | T-H8 | pending |
| 13 | docs/buddy-brain-bench.md, sprints/BACKLOG.md | docs, TD1-TD6 | doc tests | pending |
| 14 | eval/buddy_brain/runs/<id> | reference run | owner/orchestrator runs the full proof | deferred per instructions |

## Execution

## Architect feedback required

## Final state
