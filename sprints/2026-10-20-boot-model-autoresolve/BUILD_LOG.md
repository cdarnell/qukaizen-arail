# Build log: boot-time model auto-resolution

**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `8aaf4a8d`
**Started:** 2026-10-20

## Plan

Order follows ARCHITECTURE.md "Recommended implementation order".

| # | Files | Change | Test | Commit ref |
|---|---|---|---|---|
| 1 | router/backends.py, tests/router/test_keep_alive.py | keep_alive int -1 (F1); surface Ollama error text (FM7/FM8) | test-first | pending |
| 2 | model_resolve.py (new), tests/test_model_resolve.py | fit/candidate helpers + resolve_boot, no wiring | test-first | pending |
| 3 | model_defaults.py, tests/test_model_defaults.py | v2 yaml: write_cache/write_pin/v1 migration; CLI | test-first | pending |
| 4 | registry/store.py, tests | reconcile_from_resolution; drop Qwen fallback (F2/FM12) | test-first | pending |
| 5 | portal/app.py | resolver task, stamp, reconcile, delete _export_registry_env, A boot warm | integration | pending |
| 6 | deep_policy.py, app.py | B boot warm, boot_warm_safe, crash sentinel, /api/instance fields | unit | pending |
| 7 | portal/models_api.py, chat default | /api/models/boot v2, /api/models/pin, settle alias | TestClient | pending |
| 8 | _model_boot_banner.html | degraded strip; DOM tests | DOM | pending |
| 9 | scripts/start.sh, status, docs/cli.md, yaml example, CLAUDE.md | docs and CLI | cli tests | pending |
| 10 | - | clean-machine and Ollama-down runs | manual | pending |

## Execution

## Architect feedback required

## Final state
