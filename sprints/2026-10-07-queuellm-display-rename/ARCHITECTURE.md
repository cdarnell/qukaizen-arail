# Architecture: QueueLLM display rename + honest local cost

**Date:** 2026-10-07
**Spec:** [SPRINT.md](./SPRINT.md) at `17eb90e1` (think phase skipped; task + frozen list live in SPRINT.md)
**Base:** origin/main `5f775f1c`

## Restatement

The engine arail ships as its deep-mode "2nd inference" was renamed from AeroLLM to QueueLLM. The owner keeps seeing "AeroLLM" in the lab even though they were told the rename was done. This sprint changes every string a person *reads* (portal pages, the `arailctl` help and start banner, setup/upgrade/build script output, error and status messages, seeded in-app content) to say QueueLLM. It does not touch any string a *machine* reads: env var names, package/module names, backend/registry ids, JSON values, script filenames, DOM ids, sealed world bytes, the bundled NOTICE. Separately, a local QueueLLM answer reports `cloud_cost_usd ≈ $0.008`. Nothing was billed. That number is a simulated "what a 70B cloud call would have cost, ×2, floored at $0.002" figure, published under a field name that says it is a cloud cost, and read from a process-global "last record" that might belong to a different call. The fix: local answers report `cloud_cost_usd: null`, every cost field carries a source, and the result is built from the record of *this* call.

## Assumptions

- A1. The frontend never compares a display label for equality. Verified 2026-10-07: `git grep` for `=== 'AeroLLM'`-style comparisons in `src/` finds none. The only hit is `tests/test_aerollm_compute_source.py:37`. Frontend logic keys on `runtime: 'aerollm'`, `provider`, and `data-view="aerollm-*"`, and none of those change.
- A2. The chat UI does not render `cloud_cost_usd` from the chat final event today (`git grep` over templates and static JS finds no consumer). The owner saw it in the raw `/api/chat/stream` NDJSON. Changing its value to `null` therefore cannot break a renderer. It can break an external API consumer, so the key stays and only the value semantics change.
- A3. `lab/worlds/*/terms.json` (plus `spec.json` and the rest) are **sealed DDaC World bytes**. `manifest.json` pins a sha256 per file and `SKILL.md` carries `dac:world_sha256`. Editing them by hand breaks the seal. They are frozen for this sprint even though users read them in-app.
- A4. The root `NOTICE` and `THIRD-PARTY-LICENSES/aerollm/` describe the pinned v1.1.0 binary ("AeroLLM Runtime") and stay byte-identical until a separate re-pin sprint.
- A5. Registry `note` text written by `registry/store.py` is re-seeded on startup (SPRINT.md note: "the registry re-seeds from it on every start"). **Builder must verify this.** If notes persist and are not re-seeded, existing labs will keep the old text and the guard test will not catch it (see F7).
- A6. `scripts/package-aerollm-bundle.sh` is maintainer-only (it needs the private source checkout). Its output is not user-facing.
- A7. `cost_tracker.track()` returns the `CostRecord` for the call it just recorded. Verified at `src/arail/costs.py:307`.

## The rule (apply mechanically)

For every case-insensitive match of `aero\s*llm` outside the excluded paths (§Excluded paths):

1. **Comment, docstring, or HTML/CSS/JS comment** → **leave it.** Not user-visible, and churn makes review harder. (Optional cleanup is out of scope. Do not do it.)
2. **Frozen token** → **leave it.** A token is frozen if it matches any of these:
   - `[A-Z0-9_]*AERO(LLM)?_[A-Z0-9_]*` or `[A-Z0-9_]*_AEROLLM\b`: env vars (`AEROLLM_MODEL`, `AERO_*`, `ARAIL_AEROLLM_REPO`, `ARAIL_SKIP_AEROLLM_PROBE`, `LAB_SHOW_AEROLLM`). Quote them verbatim inside messages.
   - `aerollm[_-]api`, `libaerollm_api`, `AeroLLMBackend`, `tier1-aerollm`, `backend_aerollm`, `aerollm-mlx`, `aerollm-cuda`, `tn-arch-aerollm`, `show_aerollm`, `aerollm_status`, `aerollm_model`, `aerollm_preload_loop`, `_record_aerollm_bench`, `optimize-aerollm` (skill id/dir)
   - file and path names: `[\w./-]*aerollm[\w.-]*\.(sh|md|py|toml)`, `research/aerollm`, `THIRD-PARTY-LICENSES/aerollm`, `sprints/…aerollm…`
   - the `arailctl` CLI alias `aerollm` (`arailctl:271`, `arailctl:1300`)
3. **A string literal whose entire value is identifier-shaped and lowercase** (`^[a-z0-9_:.-]*aerollm[a-z0-9_:.-]*$`, e.g. `"aerollm"`, `'aerollm'`, `value="aerollm"`) → **id, leave it.** This covers `backend == "aerollm"`, `runtime: 'aerollm'`, dict keys, and radio values.
4. **Everything else inside a string literal, template text, attribute (`title=`, `aria-*`, `placeholder`), shell `info|warn|err|echo|printf|cat <<EOF` output, `log.*` message, or YAML/Markdown prose shipped in `src/`** → **rename.**
   - `AeroLLM` / `aeroLLM` → `QueueLLM` (one canonical casing; `aeroLLM` was never a brand, it was drift)
   - all-caps display such as `DEEP · AEROLLM` (CSS `text-transform` on "Deep · aeroLLM") → source text `Deep · QueueLLM`
   - lowercase prose `aerollm-tuning` (link text) → `QueueLLM tuning`
5. **Migration notes are allowed and must stay:** text matching `formerly AeroLLM` or `AeroLLM was renamed` (for example `README.md:97`). These help users who have the old name in their `.env`.
6. **Dict keys stay; dict display values change.** `{"aerollm": "AeroLLM"}` becomes `{"aerollm": "QueueLLM"}`.
7. **URLs** to `github.com/cdarnell/aerollm` or `github.com/cdarnell/qukaizen-aerollm` → `github.com/cdarnell/qukaizen-queuellm`. The repo was renamed and the old URLs only redirect. Link text follows rule 4.

### Excluded paths (frozen wholesale)

`sprints/`, `docs/archive/`, `retros/`, `learnings/`, `research/aerollm/` (origin docs, per repo CLAUDE.md), `CHANGELOG.md` (historical entries), `NOTICE`, `THIRD-PARTY-LICENSES/`, `licenses/`, `lab/worlds/**` (sealed, see A3), `models/graduated/**` (training data), `eval/**` (pinned corpora), `uv.lock`, `pyproject.toml` (package pin), `.env.example` **env var lines** (prose comments in `.env.example` follow the rule), `tests/` (handled in §Tests asserting old strings).

## Inventory: display strings to change

Line numbers are at `5f775f1c`. "≈" means the line holds the string inside a multi-line literal. The builder re-runs the guard (T-GUARD) to confirm nothing was missed, so the guard, not this table, is the final authority.

### Portal templates (HTML/JS text the browser renders)

| file:line | current | becomes |
|---|---|---|
| `src/arail/portal/templates/chat.html:1610` | `title="Deep model — served by AeroLLM, …"` | `served by QueueLLM` |
| `chat.html:1611` | `<span class="qb-label">Deep · aeroLLM</span>` (renders "DEEP · AEROLLM") | `Deep · QueueLLM` |
| `chat.html:1824` | link text `AeroLLM ↗`, href `github.com/cdarnell/aerollm` | `QueueLLM ↗`, href `…/qukaizen-queuellm` |
| `chat.html:2113` | badge `title="… (AeroLLM resident · AirLLM layer-streamed)"` | `QueueLLM resident` |
| `chat.html:2737, 2766, 2768` | `Deep · aeroLLM` (compare columns) | `Deep · QueueLLM` |
| `chat.html:2739` | `The deep model (aeroLLM) ships …` | `(QueueLLM)` |
| `chat.html:2767` | `'resident via aeroLLM'` | `'resident via QueueLLM'` |
| `chat.html:2795, 2801, 2808` | `flashStatus('… deep model (aeroLLM) …')` | `(QueueLLM)` |
| `chat.html:2803` | `aeroLLM isn’t built — run ./arailctl deep rebuild` | `QueueLLM isn’t built …` |
| `chat.html:2845` | `<span>Deep model · aeroLLM</span>` | `Deep model · QueueLLM` |
| `chat.html:2852` | `'aeroLLM isn’t available on this box.'` | `QueueLLM …` |
| `chat.html:2863` | `AeroLLM ships on the maximus tier.` | `QueueLLM …` |
| `chat.html:2912` | `'Resident once loaded — aeroLLM keeps its model …'` | `QueueLLM keeps …` |
| `chat.html:1818` | `ref-note` text listing backend adapters (verify; rename if it contains AeroLLM) | — |
| `_graph_canvas.html:692` | health row `Deep … Aero…` (rendered value) | `QueueLLM` |
| `_model_boot_banner.html:97` | `which one should aeroLLM reference for deep answers?` | `QueueLLM` |
| `_model_boot_banner.html:105` | `B — aeroLLM deep reference` | `B — QueueLLM deep reference` |
| `_model_switcher.html:116` | `' @ aeroLLM'` | `' @ QueueLLM'` |
| `_window_modal.html:22` | `AeroLLM inference, aggressive autoresearch …` | `QueueLLM inference` |
| `admin.html:929` | `<strong>Deep AeroLLM:</strong>` | `Deep QueueLLM:` (keep `health.aerollm_model`) |
| `research.html:34` | `title="Use the aeroLLM deep model …"` | `QueueLLM` |
| `research.html:304` | link text `aerollm-tuning` | `QueueLLM tuning` (href `/tuning` unchanged) |
| `research.html:317` | `<span>AeroLLM</span>` (radio label; `value="aerollm"` at :316 stays) | `QueueLLM` |
| `research.html:898` | fallback `'aeroLLM'` display name | `'QueueLLM'` |
| `research.html:899` | `@ aeroLLM` | `@ QueueLLM` |
| `research.html:908` | `'🔒 aeroLLM not installed'` | `QueueLLM not installed` |
| `tuning.html:328, 332` | tab labels `AeroLLM MLX`, `AeroLLM CUDA` (`data-view` stays) | `QueueLLM MLX/CUDA` |
| `tuning.html:345` | `AeroLLM — layer streaming on Apple silicon.` | `QueueLLM — …` |
| `tuning.html:764, 766, 774, 781, 783, 784, 787` | `heroTitle` / `archTitle` / `archBlurb` / `framingBody` strings | `QueueLLM …` (keys `"aerollm-mlx"` etc. stay) |
| `welcome.html:724` | `'… Nucleus, AeroLLM — 32 sourced terms.'` | `QueueLLM` (**see F8:** the count refers to the sealed world, which still says AeroLLM) |

Comments only, so **leave**: `chat.html:2083, 2834, 4019`, `research.html:27, 874`, `tuning.html:164, 322-323, 338, 497, 756, 876`, `_model_boot_banner.html:5`, `static/style.css:2177, 2267`.

### Python user-facing strings

| file:line | surface | becomes |
|---|---|---|
| `src/arail/model_defaults.py:288, 291` | start banner `B (aeroLLM):` (shown by `arailctl start` and the defaults printer) | `B (QueueLLM):` (keep the column alignment: the new label is 1 char longer, so adjust the padding so the columns still line up) |
| `model_defaults.py:125` | comment line written into the user's `model_defaults.yaml` | `QueueLLM` (user-read file content) |
| `src/arail/portal/app.py:1945` | `"The AeroLLM engine isn't built on this machine …"` | `QueueLLM` |
| `app.py:4341` | activity line `"… use the aeroLLM deep model."` | `QueueLLM` |
| `app.py:4395` | truth-strip detail `"… the aeroLLM deep model …"` | `QueueLLM` |
| `app.py:6958` | graph `backend_labels["aerollm"] = "AeroLLM"` | value `QueueLLM` |
| `app.py:8428` | note `"in-process deep backends (aeroLLM/AirLLM) …"` | `QueueLLM/AirLLM` |
| `app.py:8563` | `_OPTIONAL_CHAT_BACKEND_CONFIG["aerollm"]["label"] = "AeroLLM"`, which produces **"AeroLLM isn't ready on this lab"** at `app.py:9422` | `"QueueLLM"`. `class_name`, `model_env`, and `install_command` stay. |
| `app.py:9872` | deep slot `"label": "AeroLLM"` | `"QueueLLM"` |
| `app.py:10264` | `_display_provider_name` `"aerollm": "AeroLLM"` (provider chip) | `"QueueLLM"` |
| `src/arail/router/backends.py:1605` | `"AeroLLM model dir not found: …"` | `"QueueLLM model dir not found: …"` |
| `src/arail/registry/binding.py:111` | `"aeroLLM runtime unavailable …"` | `QueueLLM` |
| `src/arail/registry/ceiling.py:102` | `"… Use it as the AeroLLM secondary …"` | `QueueLLM` |
| `ceiling.py:124` | `"Pick a smaller AeroLLM/AirLLM model …"` | `QueueLLM/AirLLM` |
| `src/arail/registry/core.py:419` | `"{name} @ aeroLLM"` | `@ QueueLLM` |
| `src/arail/registry/store.py:283` | registry `note="Tier 1 deep reasoning via aeroLLM …"` | `QueueLLM` (see A5/F7) |
| `src/arail/portal/models_api.py:381` | `"label": "aeroLLM deep reference"` | `QueueLLM deep reference` |
| `src/arail/portal/model_warmth.py:116` | `"Deep model preload failed (aeroLLM unavailable) …"` | `QueueLLM` |
| `src/arail/agents/deep_policy.py:84` | reason `"… disables background aeroLLM"` (surfaced by deep-policy explain) | `QueueLLM` |
| `src/arail/experiments/levers.py:110` | `"means changing AeroLLM itself, not a lab setting."` | `QueueLLM itself` |
| `src/arail/skills/goal_parser/__init__.py:334` | `"GPU or AeroLLM"` (shown as a goal prerequisite) | `GPU or QueueLLM` |
| `src/arail/agents/builtin_seed.py:≈288–419` | seeded research-program prose (`auto_goal: Optimize AeroLLM's …`, plan text, `[AeroLLM source](…qukaizen-aerollm)`, `prepare.py` header) written into the user's `lab/pkb/research/` | `QueueLLM`; URL per rule 7. Only affects freshly seeded labs (F9). |
| `src/arail/pkb_seed.py:408` | seeded PKB text `(AeroLLM secondary — …)`; keep `AEROLLM_MODEL=` | `QueueLLM secondary` |
| `src/arail/chat/models_catalog.yaml:111, 128, 135, 318, 319` | `name:` / `description:` values shown in the model picker (the comments at :97, :99, :117, :119 stay) | `QueueLLM` |
| `lab/tools/benchmark_models.py:340` | `log.warning("AeroLLM benchmarking not yet implemented")` | `QueueLLM` |
| `lab/tools/benchmark_models.py:362` | `"aerollm backend not yet wired"` (prose, has spaces, so rule 4 applies) | `"QueueLLM backend not yet wired"` |

Docstrings and comments in `deep_policy.py`, `researcher.py`, `hardware.py`, `model_specs.py`, `tier.py`, `autochecks.py`, `experiments/*`, `registry/__init__.py`, `pkb.py:1092`, `app.py` (1178, 1781, 7399, 7544-5, 8528, 8672, 8701, 8783, 9432-9474, 9779-9883, 10163, 10203, 10469, 10612-10626, 13581-13993), `backends.py:1349, 1501, 1510, 1976, 1981`, and `_builtin_buddy.py:181` → **leave**.

### Skill packs shipped in `src/` (rendered in-app and fed to agents)

Prose in `src/arail/skill_packs/model-building/frontier-local-models/SKILL.md` (lines 9, 20, 28, 37, 48, 56, 62, 80, 98), `optimize-aerollm/SKILL.md` (title/name at 2 and 4, and lines 9, 10, 20, 37, 43, 49, 62, 71, 105), `understanding-precision/SKILL.md` (19, 61, 92, 101, 103), and `onboarding/setup-arail/SKILL.md:55` → `QueueLLM`. The directory `optimize-aerollm/` and any `id:`/`slug` stay. Keep `AEROLLM_MODEL=` verbatim. **Builder: check `skill_packs/manifest.yaml` for content hashes before editing. If they are pinned, regenerate them through the repo's own tool, never by hand.**

### `arailctl` and scripts (terminal output)

| file:line | becomes |
|---|---|
| `arailctl:255` | help text `deep <op>  QueueLLM 2nd inference: …` (`:271` alias `aerollm` stays) |
| `scripts/build-aerollm.sh:155, 165, 171, 199, 225, 257, 261, 366, 398` | every `info`/`warn`/`err` saying `AeroLLM` → `QueueLLM` (filename and `aerollm_api` import stay) |
| `scripts/setup.sh:672, 675, 678, 680, 685, 687, 694, 1274, 1327, 1561, 1566` | `info`/`warn`/help text → `QueueLLM` (keep `ARAIL_SKIP_AEROLLM_PROBE`, `aerollm_api`, `AEROLLM_MODEL_ID`) |
| `scripts/setup.sh:1578` | `printf` of a comment *into the user's `.env`*: `# AeroLLM KV-cache budget …` → `QueueLLM` (the env var name on the next line stays) |
| `scripts/upgrade.sh:131, 133, 138, 140, 141` | `info`/`warn` → `QueueLLM` |
| `scripts/blueprint.sh:82` | table column header `'AeroLLM'` → `'QueueLLM'` (`aerollm_status` key at :86 stays; width `<11` still fits) |

Comments at `arailctl:782, 785`, `setup.sh:90, 97, 129, 654, 669, 1305, 1552, 1569`, `upgrade.sh:124-126`, and all of `package-aerollm-bundle.sh` (maintainer-only, A6) → **leave**.

### Docs users read

- `docs/cli.md:196, 373, 383`: prose → QueueLLM. Keep `$ARAIL_AEROLLM_REPO`. Also rename the lowercase prose hits among the other 20 lowercase matches that are not commands or env vars (apply the rule line by line).
- `README.md:97`: a migration note. **Keep** (rule 5).
- `config/tuning.yml:68, 97` and `config/tuning-mlx.yml:266, 296`: YAML `description` values rendered on `/tuning` → QueueLLM. **Builder: confirm they are rendered.** If they are only comments, leave them.
- `catalog/models.toml:275, 292`: `notes` values (rendered by `blueprint.sh` and the catalog UI) → QueueLLM. The `:120` comment stays.

### Frozen occurrences (non-exhaustive, by category; the rule decides)

Env vars everywhere. `aerollm_api` imports. The `"aerollm"` backend id and every `== "aerollm"`, `in ("airllm","aerollm")`, and `runtime: 'aerollm'`. `BACKEND_MAP["aerollm"]`. `AeroLLMBackend`. `tier1-aerollm`. DOM ids and `data-view`. `localStorage` values `aerollm-mlx`. `show_aerollm`. Script filenames. The `arailctl` alias. `pyproject.toml` pin. `NOTICE`. `THIRD-PARTY-LICENSES/aerollm/`. Sealed `lab/worlds/**`. Historical records. `src/arail/nucleus/runtime_names.py` (it implements the alias mapping itself).

## Cost: root cause

Trace of `/api/chat/stream` for a deep (QueueLLM) turn:

```
POST /api/chat/stream (app.py:8435)
  └─ _chat_stream_events (deep branch, app.py:≈7700)
       ├─ deep_backend.complete(...)                      -> ModelResponse(backend="aerollm")
       ├─ cost_tracker.track(backend="aerollm", ...)      (app.py:7711)
       │    ├─ _classify_model("aerollm", m) -> "70b"     (costs.py:119-123: every aerollm/airllm model priced as 70B)
       │    ├─ raw = tokens_in*0.60/1e6 + tokens_out*0.90/1e6
       │    ├─ billed = max(raw * SIM_BILL_USAGE_MULTIPLIER(2.0), SIM_BILL_MIN_CALL_USD(0.002))
       │    └─ history[-1]["cloud_cost_usd"] = billed     (costs.py:408: simulated SaaS bill)
       └─ yield {"type":"final", **_build_chat_result(response, ...)}
             └─ cloud_cost_usd = cost_tracker.get_last_record()["cloud_cost_usd"]   (app.py:7263-7268)
```

Four defects stack up:

1. **Mislabelled quantity.** `cloud_cost_usd` holds a *simulated what-if* (`cloud_equivalent`, ×2 multiplier, $0.002 floor) and the field name presents it as a real cloud charge. For a local backend the true cloud cost is zero by construction, and the honest value for "cloud cost" is "not applicable". The owner's $0.008267 matches a prompt of ~6.8k estimated tokens (`len(prompt)//4`, a guess, not a count) at 70B pricing, ×2.
2. **Wrong pricing class.** Every aerollm model is priced as 70B regardless of its size (Qwen3-30B-A3B → "70b"). This is moot for the cloud-cost field once (1) is fixed. It still distorts `cloud_equivalent` totals (debt, below).
3. **No source label.** Nothing in the final event says the number is simulated, so it violates "every cost has a source".
4. **Racy attribution.** `get_last_record()` reads a process-global singleton. Agents, researcher passes, and the openai-compat API all call `track()` concurrently, so the chat final event can report another call's cost. Also, `_build_chat_result` reads `last.get("energy_cost_usd")`, but history stores `energy_usd` (`costs.py:409`), so `energy_cost_usd` is **always `None`**. That is a latent key-mismatch bug.

## Cost: fix

**costs.py**
- Add `COST_SOURCE_BY_BACKEND`, an explicit allowlist (not a blocklist):
  - `{"claude": "billed_estimate", "huggingface": "billed_estimate", "openrouter": "billed_estimate"}` → a real cloud charge that we estimate
  - `{"mlx","cuda","cpu","airllm","aerollm","ollama_native"}` → `"local"`
  - anything else, including `openai_compat` (it may be LAN Ollama or a paid gateway) → `"unpriced"`
- Add `cost_source(backend) -> Literal["billed_estimate","local","unpriced"]`.
- `track()` additionally records, on both the history dict and the `CostRecord`:
  - `cloud_cost_usd`: `round(billed,6)` only when the source is `billed_estimate`, otherwise `None`
  - `cloud_cost_source`: the value above
  - `cloud_equivalent_usd`: the existing simulated figure, unchanged math
  - `cloud_equivalent_source: "simulated"`
  - `energy_cost_usd` (alias of `energy_usd`) with `energy_source: "estimated"`
- Do not change aggregates (`total_cloud_usd`, `cloud_by_backend`, nav-bar "Cloud equiv"/"Net saved"). They are labelled "equivalent"/"saved" and are explicitly simulated. Rewiring them is out of scope.

**app.py `_build_chat_result`**
- New keyword `cost_record: dict | None = None`. Every branch that calls `track()` itself (deep at :7711, runtime at :7761, non-stream at :7895 and :7927) passes the record it got back.
- The router branch (`router/core.py:266/366` tracks internally): fall back to `get_last_record()` **only if** its `backend` and `model` equal `response.backend`/`response.model`. Otherwise emit `cloud_cost_usd: None, cloud_cost_source: "unattributed"`.
- Regardless of the record, if `cost_source(response.backend) == "local"` then force `cloud_cost_usd = None`. This is a belt-and-braces check that does not depend on attribution.
- Emit `cloud_cost_usd`, `cloud_cost_source`, `cloud_equivalent_usd`, `cloud_equivalent_source`, `energy_cost_usd`, and `energy_source`. The existing keys keep their names, and only their values become honest.
- `/api/costs` `last_record` (app.py:12160) passes the same new fields through.

**openai_compat.py** (`:290`, `:380`): apply the same `cost_source` gate to any `cloud_cost_usd` it emits.

No UI change is required (A2). If a UI later renders cost, it must render `cloud_cost_source`.

## Data flow

```
                   display path                                   cost path
 ┌────────────────────────────┐                    ┌──────────────────────────────────┐
 │ id "aerollm" (frozen)      │                    │ backend.complete() -> ModelResponse
 │   │                        │                    │        │ backend="aerollm"        │
 │   ├─ _display_provider_name├─> "QueueLLM" chip  │        v                          │
 │   ├─ backend_labels        ├─> graph node       │ cost_tracker.track() -> record    │
 │   ├─ _OPTIONAL_..["label"] ├─> "QueueLLM isn't  │   cost_source("aerollm")="local"  │
 │   │                        │    ready…"         │   cloud_cost_usd=None             │
 │   └─ deep slot label       ├─> picker header    │   cloud_equivalent_usd=sim (labelled)
 │ templates (static text)    ├─> HTML             │        │ (record passed explicitly)│
 │ scripts info/warn          ├─> terminal         │        v                          │
 │ model_defaults banner      ├─> arailctl start   │ _build_chat_result -> final NDJSON│
 └────────────────────────────┘                    └──────────────────────────────────┘
          ^ T-GUARD scans every box on the left
```

## Interface contracts

- **`_display_provider_name(provider)`**
  - Requires: any string.
  - Promises: `"aerollm"` → `"QueueLLM"`. Other keys are unchanged.
  - Bad input: falls through to the existing default.
- **Display labels in JSON (`label`, `inline_label`, `note`, `notes[]`, `reply`, `detail`)** are free text. No client may branch on them (A1). Ids (`provider`, `runtime`, `backend`, `id`, `entry_id`) are byte-unchanged.
- **`cost_source(backend)`**
  - Requires: str or None.
  - Promises: returns one of three literals and never raises. `None` or unknown → `"unpriced"`.
- **Chat final event**
  - Promises: `cloud_cost_usd is None` whenever `cloud_cost_source != "billed_estimate"`. Every non-null `*_usd` has a sibling `*_source`. `energy_cost_usd` is non-null when a record was attributed.
  - All pre-existing keys are still present.
- **`CostTracker.track()`**
  - Still returns a `CostRecord`. History dicts gain keys and lose none, so persisted `costs.json` written by an older build still loads (`get` with defaults).

## Failure modes

| # | Failure | Detection | Recovery |
|---|---|---|---|
| F1 | A JSON **value** the frontend keys on gets renamed (`runtime:'aerollm'`, `provider`, `data-view`, radio `value`), so the deep picker or compare silently routes nowhere | T-IDS: snapshot test that `/api/chat/compute-sources` and `/api/models` return `provider`/`runtime` `"aerollm"` and `id` `tier1-aerollm`; `chat.html` still contains `runtime: 'aerollm'` and tuning `data-view="aerollm-mlx"`; existing `test_chat_slots_contract.py` | Revert the value; only labels change |
| F2 | An env var name inside a message gets "fixed" (`QUEUELLM_MODEL`) and users set a var arail never reads | T-ENV: every `[A-Z_]*AERO[A-Z_]*` token present in `git show 5f775f1c:<file>` for the changed files is still present after the change (set equality per file) | Restore verbatim |
| F3 | The root `NOTICE` or `THIRD-PARTY-LICENSES/aerollm/` is edited, which breaks `test_root_notice_mentions_aerollm`, `test_notice_byte_identical_to_sibling_when_available`, and license compliance | Existing compliance tests; T-GUARD excludes them; `git diff --stat 5f775f1c -- NOTICE THIRD-PARTY-LICENSES` must be empty (review check) | Revert. Rebranding belongs to the bundle re-pin sprint. |
| F4 | Hash-pinned bundle metadata (`BUNDLE.json`, sha256 in `build-aerollm.sh`) is touched while editing that script's messages | Existing `test_aerollm_bundle_*`; T-PIN: the sha/tag constants in `build-aerollm.sh` are byte-equal to base | Revert the constant lines |
| F5 | Sealed world bytes (`lab/worlds/**`) are edited, which breaks `world_sha256` and the seal check | Existing world-seal tests; T-GUARD excludes the path; `git diff --stat -- lab/worlds` must be empty | Revert. Re-seal via DDaC in a follow-up. |
| F6 | **Negative tests go vacuous.** `test_model_ux_phase0_oversell_copy` bans `"streamed from disk via aerollm"`, `test_model_ux_phase0_headers.py:74`, `test_model_ux_phase0_eject_honesty.py:74`, and `tests/js/research_summary_harness.mjs:131` assert *absence* of old-name text. After the rename they pass trivially while QueueLLM oversell copy could slip in. | T-NEG: each banned phrase list is parameterised over both names; a meta-test asserts that every banned phrase containing `aerollm` has a `queuellm` twin | Extend the lists |
| F7 | Persisted registry notes or `model_defaults.yaml` comments in an existing lab keep "aeroLLM", so the owner still sees it after upgrading | T-RESEED: start the portal against a temp lab dir seeded with an old-text `store` entry and assert the served note is new text, or document that it does not re-seed | If notes persist: re-seed `note` for built-in entries on startup (built-in ids only, never user entries). Generated `model_defaults.yaml` comments are cosmetic, so document it in BUILD_LOG. |
| F8 | In-app glossary (sealed `terms.json`) still defines "AeroLLM", which contradicts the UI | Known and accepted; recorded as follow-up debt | DDaC world re-seal ticket |
| F9 | Seeded PKB/research files in existing labs (`lab/pkb/research/…`, from `builtin_seed.py`) keep old text | Same as F8; only fresh seeds change | Documented; do not rewrite user files (they may be edited) |
| F10 | `cloud_cost_usd` becomes `None` and a consumer does arithmetic on it (`toFixed`, `sum`) | T-COST-NULL: grep shows no template/JS consumer (A2); unit test that `/api/costs` and openai_compat handle a `None` last record; `test_openai_compat.py` still green | Consumers must treat `None` as "n/a" |
| F11 | A cloud backend gets classified `local` (allowlist mistake), so a real charge is hidden | T-COST-CLASS: table test over every `BACKEND_MAP` key, each asserted to a specific source; a new backend without a mapping → `"unpriced"` (never `local`) | Fix the table |
| F12 | Cost attributed to the wrong call (race) | T-COST-RACE: two concurrent fake turns (one `aerollm`, one `claude`) with interleaved `track()`; each final event carries its own source and value | Explicit record passing; mismatch → `unattributed` |
| F13 | Banner column misalignment: `B (QueueLLM):` is 1 char wider than `B (aeroLLM):` | T-BANNER: `test_model_defaults.py` asserts the label and that the A and B model columns start at the same offset | Adjust the padding |
| F14 | Guard test false positives (comments, ids) cause the builder to "fix" frozen ids to get green | Guard is token-aware (strips comments, applies rule 2/3 allowlist); allowlist changes need a reviewer-visible reason string | Extend the allowlist with a reason; never rename the id |
| F15 | A `.sh` edit breaks the script (quoting, `set -u`) | `bash -n` on every changed script; existing `test_aerollm_local_sibling_build.py`, which runs the script | Fix quoting |

## Tests asserting old strings: how to handle them

- **Positive assertions on display text** → retarget to the new text in the same commit as the source change:
  - `test_aerollm_compute_source.py:37` → `"QueueLLM"`
  - `test_aerollm_defaults.py:207` → `"QueueLLM model dir not found"`
  - `test_aerollm_local_sibling_build.py:36` → `"QueueLLM"`
  - `test_aerollm_model_ready.py:151` → `"QueueLLM isn’t built"`
  - `test_lever_handoff.py:49` → `"QueueLLM itself"`
  - `test_model_defaults.py:345` → `"B (QueueLLM):"`
  - `test_model_ux_phase0_eject_honesty.py:56` → `QueueLLM`
  - `test_model_ux_phase0_headers.py:66` → new header
  - `test_aerollm_bundle_qa_hardening.py:481` (string-index anchor) → retarget the anchor
  - `test_program_drafter.py:117`: the source of "Tune AeroLLM stability-vs-performance levers" was not found in `src/`. Builder: locate it (fixture, `config/`, or LLM stub). If it is fixture-produced, leave it.
- **Negative assertions** → assert absence of **both** names (F6). Never delete them.
- **Frozen-surface assertions stay as they are:** `test_aerollm_bundle_compliance.py:30` (NOTICE mentions AeroLLM), every assertion on ids, env vars, `AeroLLMBackend`, and filenames.
- **Test filenames** (`test_aerollm_*.py`) are historical identifiers. Do not rename them.
- Never weaken an assertion to `"LLM" in …` to get green.

## Test strategy

- **Unit**
  - T-GUARD (new, `tests/test_no_user_visible_aerollm.py`). The guard that fails if a user-visible AeroLLM string reappears. It scans:
    - (a) `src/arail/**/*.py`: `ast` walk collecting `str` `Constant`s and `JoinedStr` parts, **excluding** module/class/function docstrings
    - (b) `src/arail/portal/templates/**/*.html` and `src/arail/portal/static/**/*.{js,css}`: strip `<!-- -->`, `/* */`, and `//`-to-EOL (outside quotes is good enough; the allowlist covers URLs)
    - (c) `arailctl` and `scripts/{setup,upgrade,build-aerollm,blueprint}.sh`: lines that are `info|warn|err|echo|printf` calls or inside the help heredoc
    - (d) `src/arail/**/*.{yaml,md}` and `docs/cli.md`, `config/tuning*.yml`, `catalog/models.toml` value text, with YAML/TOML `#` comments stripped

    Matching: in every extracted string, remove frozen tokens (rule 2), skip identifier-shaped literals (rule 3) and migration phrases (rule 5), then fail on `re.compile(r"aero\s*llm", re.I)`. The failure message prints `file:line: text`. The allowlist is a module-level tuple of `(regex, reason)` pairs. **Self-tests:** the guard must flag synthetic samples (`"AeroLLM isn't ready"`, `<span>Deep · aeroLLM</span>`, `info "AeroLLM ready"`, `title="served by AeroLLM"`) and must pass `"aerollm"`, `AEROLLM_MODEL`, `aerollm_api`, `tier1-aerollm`, `# AeroLLM comment`, and `formerly AeroLLM`. Without these self-tests a broken extractor yields a vacuously green guard.
  - T-COST-CLASS: `cost_source` for every `BACKEND_MAP` key plus `None` and an unknown string.
  - T-COST-LOCAL: `_build_chat_result` with `ModelResponse(backend="aerollm")` and a matching record gives `cloud_cost_usd is None`, `cloud_cost_source == "local"`, `cloud_equivalent_source == "simulated"`, and non-null `energy_cost_usd` (regression for the key mismatch).
  - T-COST-CLOUD: `backend="claude"` gives a non-null `cloud_cost_usd` and `"billed_estimate"`.
  - T-COST-UNATTR: a router-branch response whose last record has a different model gives `None` and `"unattributed"`.
  - T-BANNER, T-ENV, T-PIN, T-NEG (F13, F2, F4, F6).
- **Integration**
  - T-STREAM: FastAPI `TestClient` POSTs to `/api/chat/stream` with a stubbed `AeroLLMBackend.complete`. Parse the NDJSON `final` and assert the cost contract. Repeat for `/api/chat` (non-stream).
  - T-IDS (F1): API snapshots of ids. Render `chat.html` and `research.html` through the app and assert the rendered HTML contains "QueueLLM" and no rendered "aeroLLM".
  - T-RESEED (F7).
  - T-COST-RACE (F12).
  - `bash -n` over changed scripts. Run `./arailctl help | grep -ic aerollm` and expect only the alias line (`benchmark, aerollm`).
- **Regression:** the full `pytest` suite plus `node tests/js/research_summary_harness.mjs`. The existing bundle-compliance and world-seal tests must pass with no edits to their frozen assertions.
- **Performance:** n/a. The guard test must run in under 5 s.
- **Security:** no new input surface. Confirm the cost fields carry no prompt text (metadata only). Confirm no `.env` write path changed except the comment text at `setup.sh:1578`.
- **Manual (owner witness):** on a maximus lab, run one deep answer. The picker header reads "DEEP · QUEUELLM", the chip reads "QueueLLM", and the raw final event shows `"cloud_cost_usd": null, "cloud_cost_source": "local"`.

## Tech debt

**Added:**
- Mixed vocabulary is now *deliberate and visible*: users read "QueueLLM" but set `AEROLLM_MODEL` and run `build-aerollm.sh`. The migration notes (rule 5) mitigate this. The real fix is the frozen-surface sprint already named in the workspace CLAUDE.md.
- The guard allowlist is a new thing to maintain.
- Final-event schema gains 4 keys.

**Repaid:**
- The owner-reported naming drift is gone, and a guard now prevents regression.
- The fabricated cloud cost is gone. Every cost field has a source.
- Racy `get_last_record()` attribution and the always-`None` `energy_cost_usd` key bug are fixed.
- Two casings (`AeroLLM`/`aeroLLM`) collapse to one.

**Net:** negative. Follow-ups to file in SPRINT.md:
1. DDaC world re-seal for the `terms.json` "AeroLLM" entries (F8).
2. `_classify_model` prices every aerollm/airllm model as 70B, which inflates the nav-bar "Cloud equiv"/"Net saved" simulated totals.
3. Frozen-surface rename sprint (env vars → `QUEUELLM_*` before engine 2.0.0, package, ids, NOTICE re-pin).
4. The SPRINT.md UX traps (`model_defaults.yaml` overriding `.env`, the stale start banner, the advisory "requires streaming" chip) stay out of scope.

## Recommended implementation order

1. **Cost fix first** (smallest, independent, highest honesty value):
   - `costs.py` `cost_source` + new record fields
   - `_build_chat_result(cost_record=…)` + call sites
   - `/api/costs` and openai_compat
   - T-COST-* and T-STREAM

   One commit.
2. **Guard test, with self-tests, expected to FAIL** listing the current hits. Commit it as `xfail(strict=True)` so the inventory is machine-checked from here on.
3. **Python display strings** (app.py label maps first, then registry, router, model_defaults banner, and the rest) + retargeted positive tests + T-BANNER and T-IDS. One commit.
4. **Templates** + T-NEG extensions (oversell, headers, eject, JS harness). One commit.
5. **arailctl and scripts** + `bash -n` + T-ENV and T-PIN. One commit.
6. **Seeded content, YAML/TOML, skill packs, `docs/cli.md`** (check the skill manifest hashes first). One commit.
7. Flip the guard from `xfail` to a normal test. The full suite must be green. Record F7/F8/F9 outcomes and follow-up tickets in BUILD_LOG.md.
