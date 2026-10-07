# Build log: queuellm-display-rename

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `20726778`
**Started:** 2026-10-07

## Plan

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | `src/arail/costs.py`, `src/arail/portal/app.py` | `cost_source()`, honest cost fields, explicit record passing into `_build_chat_result`, `/api/system/costs` passthrough | T-COST-CLASS/LOCAL/CLOUD/UNATTR/RACE, T-STREAM (test-first) | pending |
| 2 | `tests/test_no_user_visible_aerollm.py` | token-aware guard + self-tests, `xfail(strict=True)` | self-tests green, guard xfails | pending |
| 3 | Python display strings (app.py maps, registry, router, model_defaults, ...) + retargeted tests | rename per rule | T-BANNER, T-IDS, retargeted positives | pending |
| 4 | portal templates + negative-assertion tests | rename per rule | T-NEG, JS harness | pending |
| 5 | `arailctl`, `scripts/*.sh` | rename messages | `bash -n`, T-ENV, T-PIN | pending |
| 6 | seeded content, YAML/TOML, skill packs, `docs/cli.md` | rename per rule | manifest hash check | pending |
| 7 | guard test | drop `xfail`; record F7/F8/F9 | full suite | pending |

Test runner: `PYTHONPATH=src /Users/netsushi/ProJects/qukaizen-arail/.venv/bin/python -m pytest` (this worktree has no venv of its own; `PYTHONPATH=src` makes the shared venv import this worktree's code).

## Execution

## Architect feedback required

## Final state
