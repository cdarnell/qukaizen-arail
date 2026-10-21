# Test report: boot-time model auto-resolution

**Date:** 2026-10-21
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) round 2 at `579d6038`; branch HEAD under test `a248e8e3` (QA commits on top: `7f82beef`, `bccec418`, `dbc0b1db`, `47ba7830`, `2cac4aa4`)
**Baseline:** merge-base `ac83fc47` (exported to a scratch dir, not a worktree)
**Verdict:** PASS (final, after round 2 below; round 1 was FAIL)

One HIGH defect (QA-1) that the sprint's own fixtures could not see. On a fresh **maximus** setup on Apple Silicon, `setup.sh` writes `MODEL_BACKEND=mlx` and `MODEL_NAME=mlx-community/Qwen3-8B-4bit`. On every boot the resolver then stamps the Ollama tag `llama-ai-eng:latest` into `MODEL_NAME`, so the env-built primary router (`ModelRouter()`) gets an MLX backend paired with an Ollama tag. This was reproduced on a real `./arailctl start`: the boot warm dies with `HFValidationError`, and every `ModelRouter()` caller inherits the broken pair. That includes the portal's primary router, librarian scout, dictionary, world routes and the goal parser. Base leaves the MLX pair intact. Everything else the brief asked for holds.

## Test inventory

Allocation follows the ARAIL CLAUDE.md split: 30% setup / 30% Buddy+model behaviour / 20% security / 10% happy / 10% regression. Test functions live in `tests/test_boot_autoresolve_qa.py` unless another file is noted. "Real boot" means `./arailctl start --root --no-browser` in a scratch clone with a pinned `.env`, an empty `ARAIL_MODELS_DIR`, and repo-relative `lab/` (harness: scratchpad `boot_bench.py`, `scenarios.py`).

| # | Test | Category | Covers | Status |
|---|---|---|---|---|
| 1 | `test_setup_maximus_mlx_env_keeps_a_loadable_primary_backend_model_pair` | setup | setup.sh maximus/mlx `.env` shape vs resolver env stamping | **FAIL (QA-1)** |
| 2 | Real boot, maximus/mlx `.env`, branch vs base | setup | same, end to end | **FAIL on branch** (warm_a failed, HFValidationError); base keeps the pair |
| 3 | Real boot, minimalist `.env` (`ollama_native`, `llama-ai-eng`), 5+5 runs | setup / perf | time-to-ready, warm A real | PASS (warm A "weights resident") |
| 4 | Real boot, `.env` without `MODEL_BACKEND`, 9+9 runs | setup / perf | time-to-ready | PASS for ready; warm A fails the same way as QA-1 (`auto` maps to mlx), hand-made-lab case only |
| 5 | `test_odd_models_dir_resolves_b_none_without_raising[missing,file]` | setup | `ARAIL_MODELS_DIR` absent or a file | PASS |
| 6 | `test_yaml_in_a_directory_that_does_not_exist_still_boots` | setup | yaml parent missing | PASS |
| 7 | `test_only_an_embedding_model_installed_gives_a_none_with_the_setup_command` | setup | FM2/FM15 with real capability answer | PASS |
| 8 | `test_unicode_model_name_round_trips_through_the_cache` | setup | unicode pin + cache | PASS |
| 9 | `test_cli_banner_json_get_survive_garbage_yaml` | setup | `python -m arail.model_defaults` CLI on garbage file, bytes kept | PASS |
| 10 | `tests/cli/*_driver.sh` (13 drivers, `ARAIL_TEST_VENV` = main venv, PYTHONPATH pinned to the tree under test) | setup / regression | CLI layer | 8 PASS, 5 FAIL. The failing set is **identical on base** (see Notes) |
| 11 | Real boot: Ollama down (ollama shimmed off PATH, dead `OLLAMA_HOST`) | model | FM1 | PASS: ready 1.32 s, `degraded/ollama_down`, warm A/B skipped, no traceback |
| 12 | Real boot: pinned model missing (`ghost-model:7b`) | model | FM3 | PASS: A auto `llama-ai-eng`, strip names `ollama pull ghost-model:7b`, pin preserved |
| 13 | Real boot: operator's v1 shape (`default_a: ai-engineer:latest`, `default_b: null`, env `AEROLLM_MODEL=Qwen…`) | model / regression | F2, FM11 | PASS: A pin, B "pinned off", `pins.b: none`, `.model_defaults.v1.bak`, mode hidden, no drift text |
| 14 | Real boot: garbage yaml (binary + broken flow) | model | FM10, B1 | PASS: bytes unchanged, honest `cache_unsaved` note |
| 15 | Real boot: v2 hand-edited with a bad-indent typo | model | FM10, B1 | PASS: bytes unchanged, same note |
| 16 | Real boot: stale crash sentinel (dead pid, 30 s old) | model | FM5 | PASS: boot clean, sentinel kept (no deep model on disk, so no note) |
| 17 | Real boot: `ARAIL_BOOT_WARM=0` | model | opt-out | PASS: warm_a/b `disabled`, resolution still cached |
| 18 | `test_garbage_yaml_never_blocks_boot_and_is_never_silently_destroyed` ×12 | model | binary, list, scalar, empty, pins-as-list, string/future version, v1 `None`/`''`, typo, unsafe tag | PASS |
| 19 | `test_unsafe_yaml_tag_is_not_executed` | model / security | `!!python/object/apply` | PASS (safe_load) |
| 20 | `test_v1_string_none_for_b_keeps_the_deep_slot_off` | model | v1 `default_b: None` (string) | PASS |
| 21 | `test_unreadable_yaml_with_b_none_does_not_auto_pick_a_deep_model` | model | B1 aftermath | **XFAIL strict (QA-3, LOW)** |
| 22 | `test_malformed_tags_body_never_escapes_the_resolver` ×7 | model | `/api/tags` list/str/bad size/bad details/not JSON/empty | PASS |
| 23 | `test_resolve_boot_never_raises_on_malformed_tags` | model | resolver contract | PASS |
| 24 | `test_odd_sentinels_never_raise_and_never_wedge` ×11 | model | empty/invalid JSON, pid str/-1/0, bad or future timestamp, null model | PASS |
| 25 | `test_sentinel_with_an_out_of_range_pid_is_stale_not_an_exception` | model | pid > INT_MAX | **XFAIL strict (QA-5, LOW)** |
| 26 | `test_stale_sentinel_skips_b_on_a_real_boot_path` | model | FM5 with a real candidate | PASS |
| 27 | `test_boot_warm_zero_never_touches_either_slot` | model | opt-out at unit level | PASS |
| 28 | `test_keep_alive_env_values_are_typed_for_ollama` ×6 | model | `" -1 "`, `0`, `+5`, `5m`, `1.5`, `-1m` | PASS |
| 29 | Live Ollama 0.32.4: `/api/chat` with `keep_alive:"-1"` vs `-1`, models `llama-ai-eng`, `ai-engineer` | model / regression | F1 root cause | PASS: string gives 400 "missing unit", int gives 200 for both |
| 30 | Live: real `OllamaNativeBackend` for the tier-0 model after `resolve_and_apply` | model / regression | F1 end to end | PASS: `_keep_alive()` is `-1` (int), completion 200 |
| 31 | `tests/test_model_resolve_integration.py` + `tests/router/test_keep_alive.py` with live Ollama up | model | 3 `requires_ollama` tests actually ran (no skips) | PASS (25/25) |
| 32 | `test_pin_b_rejects_traversal_absolute_and_garbage` ×10 | security | `../`, `/etc`, `/`, `~`, `sub/../../`, 10 KB, `..`, `.` | PASS |
| 33 | `test_pin_b_with_a_nul_byte_is_a_400_not_a_500` | security | NUL byte | **XFAIL strict (QA-4, LOW)** |
| 34 | `test_pin_b_rejects_symlink_escaping_the_models_dir` | security | symlink out of models dir | PASS |
| 35 | `test_pin_rejects_malformed_bodies_without_a_500` ×7 | security | bad slot, non-string model, NoSQL-ish object, `{}`, `[]` | PASS |
| 36 | `test_pin_a_with_a_bogus_tag_suffix_never_reaches_env_or_registry` | security | `llama-ai-eng:../../etc` | PASS (stored pin is junk, stamped model is installed; QA-6 INFO) |
| 37 | `test_pin_errors_and_boot_payload_never_contain_secrets` | security | API key in env, credentials in `OLLAMA_HOST` | PASS |
| 38 | `test_pin_refuses_cross_site_requests` ×2 | security | Origin / Sec-Fetch-Site: a cross-site page can't clear `b: none` | PASS |
| 39 | `test_airgapped_remains_the_default_lab_mode` | security | default unchanged | PASS |
| 40 | `test_blank_pin_clears_to_auto` | happy | blank pin means auto | PASS |
| 41 | `test_retired_drift_and_bare_400_banners_are_gone_from_shipped_code` | regression | "settled value" text in src/scripts | PASS |
| 42 | `test_ollama_400_surfaces_the_real_error_not_a_bare_bad_request` | regression | FM7, no auth header in message | PASS |
| 43 | Real-boot logs (all 7 scenarios + benches) grepped for drift text and `400 Bad Request`/`400 Client Error` | regression | brief item (4) | PASS: none found |
| 44 | `test_settle_default_b_none_is_always_valid` order leak | regression | isolation fix `7f82beef` | FIXED: red after `test_aerollm_defaults.py` before, green after |
| 45 | `test_api_instance_exposes_warm_a_and_warm_b` flake | regression | isolation fix `bccec418` | FIXED: root cause reproduced deterministically (boot task records `disabled` after the setitem; 0.5 s pause makes it fail every time) |
| 46 | Full suite, branch vs base | regression | everything | PASS: branch 71F / 5711P / 34S / 10xF; base 81F. Name-by-name, the only branch-only failure is #1 |

## Failures

| # | Test | Symptom | Minimal repro | Severity |
|---|---|---|---|---|
| QA-1 | #1, #2 | With `MODEL_BACKEND=mlx` (what `setup.sh` writes for maximus on Apple Silicon, `scripts/setup.sh:1536-1543`), boot overwrites `MODEL_NAME=mlx-community/Qwen3-8B-4bit` with the Ollama tag `llama-ai-eng:latest`. `ModelRouter()` builds the MLX backend from `MODEL_BACKEND` and loads `MODEL_NAME`. Real boot activity: `Primary chat preload skipped: HFValidationError: Repo id must use alphanumeric chars … 'llama-ai-eng:latest'`, `warm_a: failed`. The start banner also tells the user to `ollama pull mlx-community/Qwen3-8B-4bit`. The sprint's fixtures always set `MODEL_BACKEND=ollama_native`, which is why it was missed. | `MODEL_BACKEND=mlx MODEL_NAME=mlx-community/Qwen3-8B-4bit`, Ollama has `llama-ai-eng:latest`, then `model_resolve.resolve_and_apply()` makes `os.environ["MODEL_NAME"] == "llama-ai-eng:latest"`. Or a real `./arailctl start` with that `.env`. | **HIGH**: breaks the primary/agent router on the default maximus Mac install path; base is unaffected. Fix direction (builder's call): slot A is the Ollama slot, so only stamp `MODEL_NAME` when the primary backend is Ollama-fronted (`ollama_native`/`openai_compat` at the Ollama port), or stamp the tier-0 registry entry without touching `MODEL_NAME` otherwise; in both cases don't treat a non-Ollama `MODEL_NAME` as a slot-A preference ("isn't installed" note) |
| QA-3 | #21 (xfail) | An unreadable yaml (B1 now preserves it) means pins are treated as none **for that boot**, so on a maximus box with a deep model on disk, auto-B is picked and warmed. If the file held `b: none` (the operator's SIGABRT workaround), the crashing model loads once; the sentinel catches it on the next boot. FM10 sanctions "pins treated as none" but did not consider this. | v2 yaml `pins: {b: none}` + one bad-indent line, one sized model under `ARAIL_MODELS_DIR`, `_deep_slot_available() is None`, then `resolve_and_apply()["b"]["model"]` is that model | LOW: needs a corrupt file plus a crashing model; bounded to one crash. Suggest: B = none while the file is unreadable |
| QA-4 | #33 (xfail) | `POST /api/models/pin {"slot":"b","model":"a\u0000b"}` gives `ValueError: embedded null byte` from `os.path.realpath`, so HTTP 500 instead of 400 | as stated | LOW: loopback, authenticated, nothing written |
| QA-5 | #25 (xfail) | Sentinel `pid` > INT_MAX: `os.kill` raises `OverflowError`, which `_pid_alive` doesn't catch. `_boot_models` catches it but then skips the whole resolve that boot (cache only) | write `{"model":"M","pid":1099511627776,"started_at":now}` to the sentinel and call `crashed_model()` | LOW: needs a hand-edited dotfile |

## Security review

| Surface | Checked | Findings |
|---|---|---|
| User input (`POST /api/models/pin`) | Traversal (`../`, `sub/../../`, `..`, `.`), absolute (`/`, `/etc`, `~`), 10 KB string, NUL byte, symlink escaping `ARAIL_MODELS_DIR`, non-string/obj/array bodies, bad slot. Read `_validate_pin` (realpath + `commonpath` containment) | All rejected 4xx except NUL (QA-4, 500). Slot A accepts any tag whose base name is installed (`llama-ai-eng:../../etc` is stored as a pin), but only an installed id reaches env/registry. This is the same bar as the old settle (QA-6, INFO). Ollama tags are never filesystem paths |
| Cross-site writes | `Origin: http://evil.example` and `Sec-Fetch-Site: cross-site` on the pin POST | 403, `b: none` intact (#38) |
| Secrets in errors/logs | API key in env and `user:secret@` in `OLLAMA_HOST`, then pin error bodies, `/api/models/boot` (+`?full=1`), `/api/instance` scanned. `_raise_for_status_with_detail` reads the response body only (#42). All real-boot logs and activity reviewed by eye | No secret in any payload. Activity lines carry model ids and the exception text of the warm (pre-existing pattern, authenticated surface only) |
| Deserialization | `model_defaults.yaml` with `!!python/object/apply:os.system` | `yaml.safe_load`; marker file not created (#19). Sentinel is `json.loads` + `isinstance(dict)` |
| Network / airgap | `_ollama_base` loopback refusal already tested for 5 hosts; credentials-in-URL host doesn't leak. `git diff ac83fc47 HEAD -- src/arail/airgap.py src/arail/config.py` is empty; `is_airgapped()` with `LAB_MODE` unset is True (#39). All real boots ran `LAB_MODE=airgapped` and only touched 127.0.0.1:11434 (Ollama access log: `/api/tags`, `/api/show`, `/api/chat` from 127.0.0.1 only) | Clean |
| File I/O | yaml atomic replace preserves unreadable files (#14, #15, #18); `.v1.bak` is created once; sentinel under per-instance `DATA_DIR` | Clean (QA-3 is a policy gap, not an I/O bug) |
| Crypto / auth / deps | No change in the diff (`pyproject.toml` +1 is a pytest marker, no new dependency) | N/A |

## Performance

Real `./arailctl start --root --no-browser`, Ollama auto-started by `start.sh`, time from exec to the first 200 on `/api/instance` (startup must finish before any request is served), runs alternated branch/base:

| Run set | Branch median (min–max, sd) | Base median (min–max, sd) | Delta | Threshold | Verdict |
|---|---|---|---|---|---|
| Minimalist setup `.env` (`ollama_native`, `llama-ai-eng`), n=5 each | 1.29 s (1.24–1.29, 0.02) | 1.27 s (1.25–1.30, 0.02) | +0.02 s | +0.20 s | PASS |
| No `MODEL_BACKEND` in `.env`, n=8 branch / 9 base (branch run 7 dropped: overlapped a stray run on the same ports) | 1.335 s (1.30–1.42) | 1.32 s (1.30–1.51, 0.07) | +0.015 s | +0.20 s | PASS |

Boot warm A completes (weights resident, `warm_a: ok`) at a median of 2.51 s after exec on the minimalist shape. Base does no boot warm without `ARAIL_AUTOCHECKS`. Seven scenario boots (#11–#17) were all ready in 1.31–1.45 s, including Ollama down. No separate BENCHMARK.md; the numbers are here.

## Coverage delta

Not measured: `pytest-cov` is not installed in the shared venv, and I did not add it to the operator's environment. Test count: +77 collected QA cases (`tests/test_boot_autoresolve_qa.py`) on top of the builder's ~180 sprint tests.

## Notes for the next QA pass

- **Every sprint fixture hardcodes `MODEL_BACKEND=ollama_native`**, which hid QA-1. When the builder fixes it, parametrize the `booted`/`env` fixtures over `ollama_native` and `mlx`.
- **CLI drivers:** 5 of 13 fail identically on base (`qa_db_ledger` A2/ASK-6, `qa_edge` QA-7 notebook not answering in 10 s, `status` T34 2.7–2.8 s ≥ 2 s, `verbs` doctor exit 3, `warmup` T23a). They are environmental and not from this sprint. `qa_edge_driver.sh` leaks its portal and memory processes (ports 18810/18811) when QA-7 fails. I killed them; worth a `trap` in the driver.
- **Worktree `.env` leak (pre-existing):** `config.py`'s bare `load_dotenv()` walks up from `.claude/worktrees/<wt>/src/arail/` and finds the operator's real `qukaizen-arail/.env`, so CLI drivers run from a worktree see the operator's `MODEL_NAME`/`AEROLLM_MODEL`. My harness pins `ARAIL_ENV_FILE` to avoid this.
- **Banner cosmetics (INFO):** with no cache yet, the `start.sh` banner prints the unvalidated env B (`Qwen2.5-7B-Instruct-4bit … source: env`) before the portal resolves. With an unreadable yaml it shows A as `llama-ai-eng size unknown` plus a pull hint even when installed. Display-only; nothing is loaded.
- The REVIEW round-2 ASKs and both BUILD_LOG debts are now in `sprints/BACKLOG.md` (`47ba7830`).
- Ollama was down at the start of QA; I started it for the live checks and stopped it afterwards. The operator's real lab, `.env` and pins were only read (one `grep ^MODEL_BACKEND=`), never written.

---

# Round 2 (re-test after builder round 3)

**Date:** 2026-10-21
**Build:** BUILD_LOG.md "Round 3" (`c753cbf2`, `e89e61c7`, `f216dba8`, `dd556019`, `1dc50ed0`, `3db78dde`); tested at `34dbb573` (includes the QA isolation fix below)
**Verdict:** PASS

## Re-verification

| # | Check | Result |
|---|---|---|
| R1 | QA-1 on a real `./arailctl start` (scratch clone, pinned `.env`, airgapped), `MODEL_BACKEND` = mlx / unset / cuda / cpu with a non-Ollama `MODEL_NAME` | PASS. No `HFValidationError` in any boot. The banner and the primary router error both carry the `.env` `MODEL_NAME` (e.g. `mlx-community/Qwen3-8B-4bit`), so it was not overwritten. Warm A: mlx/unset `failed` with `EgressBlocked: huggingface.co` (no weights in the scratch lab, and the airgap guard correctly blocks the lookup); cpu `failed` with `ImportError: llama-cpp-python` (not installed); cuda `skipped` ("loads weights in-process"). All are environment outcomes for a lab with no weights, not resolver defects |
| R2 | Same, `ollama_native` and `openai_compat` (`MODEL_API_BASE` = local Ollama) | PASS: `warm_a: ok`, "weights resident", A source `env` |
| R3 | `test_setup_maximus_mlx_env_keeps_a_loadable_primary_backend_model_pair` | PASS (was the deliberate failure) |
| R4 | QA-3/4/5 flipped xfails | PASS: all three pass as plain tests and no `xfail` markers remain (77/77). On the real boots with garbage yaml and with a v2 typo, B now resolves `none`/`pin` while the file stays byte-identical |
| R5 | Seven bad-boot scenarios rerun on a real start | PASS: ready 1.19–1.29 s; no traceback, drift text or bare 400 in any log; pins kept; v1 migrates to `b: none` + `.v1.bak`; `ARAIL_BOOT_WARM=0` leaves both slots `disabled` |
| R6 | Time-to-ready vs base, minimalist setup `.env`, 5+5 alternating runs | PASS: branch median 1.24 s (1.19–1.25, sd 0.02) vs base 1.21 s (1.20–1.22, sd 0.01), +0.03 s against the +0.20 s threshold; warm A done at a median of 2.43 s |
| R7 | `tests/test_warm_up.py::test_api_instance_world_branch_exposes_warm_fields` race | FIXED in `34dbb573` (stub `_boot_models`, the same fix as `bccec418`). With a 0.5 s pause before the GET it failed 3/3 without the fix and passed 3/3 with it; the whole file passes 8/8 |
| R8 | Full suite, by name vs base `ac83fc47` | PASS: branch 65F / 5728P / 34S / 7xF; base 81F. **Zero branch-only failures**; 17 base failures don't occur on the branch |

## Remaining notes (INFO, non-blocking)

- **Banner mislabel under a non-Ollama backend.** `start.sh` prints `A (resident): mlx-community/Qwen3-8B-4bit  1.2 GB  source: auto`, which joins the `.env` MLX name with the size and source of the Ollama slot-A resolution (`llama-ai-eng`). Display only. Suggest printing the resolved Ollama A, or labelling the primary backend separately.
- **Accepted behaviour change (A8), worth stating in the PR.** On a maximus mlx lab, the boot warm now constructs the MLX primary router in-process at every boot (main did that only with `ARAIL_AUTOCHECKS`). With real weights present, that loads Qwen3-8B at boot.
- The `start.sh` cosmetics from round 1 (unvalidated env B before the first resolve) and the CLI-driver/worktree `.env` notes still apply.
