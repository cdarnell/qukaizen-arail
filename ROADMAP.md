---
title: Roadmap
category: Reference
order: 90
tags:
  - roadmap
  - planning
  - reference
audience: operator
related:
  - BLUEPRINTS
  - CONTRIBUTING
---
# ARAIL Roadmap

> Now / Next / Later view. Living document — reorder freely. Big tracks link
> out to their own implementation plans; this file stays a one-screen index.

## Now

### Build, Model Mgmt & Fine-Tuning Pipeline → [`docs/maximus.plan.md`](docs/maximus.plan.md)

Local frontier-model lifecycle (download → register → fit → load → infer →
fine-tune → re-register). QLoRA/LoRA factory via `bitsandbytes` + `fsdp_qlora`.
OpenAPI surface, CI/CD, monitoring, RBAC, audit log.

Phases (see plan §15 for exit criteria):

- [ ] **0** — Repo layout, manifest schema, registry watcher, CLI shells.
- [ ] **1** — HF download + license gate, full-load via AeroLLM, SSE status, UI block-until-resident.
- [ ] **2** — Fit estimator + streaming/hybrid loaders + strategy chooser UI.
- [ ] **3** — Job orchestrator + finetune worker + eval hooks + artifact registration.
- [ ] **4** — Provider adapters + `/infer` with primary/fallback/compare + provenance.
- [ ] **5** — Prometheus + Grafana + tracing + audit log + RBAC.
- [ ] **6** — OIDC + Helm chart + prod secrets backend.

### Chat Studio redesign → [`docs/chat-studio.spec.md`](docs/chat-studio.spec.md)

LM-Studio-grade chat surface on top of ARAIL/AeroLLM. Stats-under-response,
per-model tunables, dual-model compare, blocking model load with ETA, in-page
credentials, fine-tune wizard. 5-milestone migration plan; legacy Jinja chat
retained behind `?legacy=1` for one release after M5.

- [ ] **M1** — Stats line + provenance overlay on existing Jinja chat.
- [ ] **M2** — Loader strip + blocking send; new ModelPicker dropdown.
- [ ] **M3** — Right-sidebar drawer (Tunables + Logs) + presets.
- [ ] **M4** — Dual composer + `/api/chat/dual-stream`.
- [ ] **M5** — Connections drawer + Fine-tune wizard.

### UI / Design system → [`docs/portal-design.md`](docs/portal-design.md)

- [x] Token scale (palette, alpha tiers, spacing, type, elevation).
- [x] Rail line under nav (signature visual).
- [x] Theme system (`LAB_UI_THEME`: `blue-cyan-lab`, `emerald-terminal`, `night-amber`, `slate-violet`) with cycle button.
- [x] Mission Status step-context.
- [x] Whisper toast component (scaffold; agent integration pending).
- [ ] Per-template `<head>` theme bootstrap to eliminate first-paint flash.
- [ ] Migrate remaining hard-coded `rgba(...)` in agents/research/knowledge CSS to tokens.

## Next

- **Speculative decoding integration** — QuKaizen swarm + AeroLLM layer-streaming for batched draft/verify (see `memory/project_speculative_decoding_integration.md`).
- **Domain expert distillation** — second flagship; needs the fine-tune factory from maximus.plan §6.
- **Mission Status ↔ chat surfacing** — clicking a step caption deep-links to the relevant chat thread.

### Autoresearch durability → [`docs/plans/autoresearch-integration.md`](docs/plans/autoresearch-integration.md)

The Tuning loop's git ledger is mechanically sound but not yet durable
over months of use. The audit documents five hazards (baseline commit
landing on the user's current branch; `arailctl update`'s `--ff-only`
refusing once it has; unbounded `autoresearch/*` branch accumulation;
unbounded committed bench `.jsonl` growth; the clean-tree gate vs. a
blueprint people fork and edit) plus six operator decisions that gate the
fixes. **Blocked on those decisions, not on implementation.**

### Production-readiness follow-ups (from sprint 2026-05-01-prod-readiness-wrappers)

- **Phase 2 — inference worker isolation.** Phase 1 (in-process `inference_slot` semaphore + fast-path bypass) shipped 2026-05-01. Phase 2 should try multi-worker uvicorn first (`--workers 2`, semaphore becomes per-process); if a single worker still saturates under sustained load, extract `arail.router` into an out-of-process inference daemon over a Unix socket so FastAPI workers stay purely I/O-bound. See `sprints/2026-05-01-prod-readiness-wrappers/ARCHITECTURE.md` § "Phase-2 callout".
- **SRE emit honors `Observation.severity`.** Today the SRE activity-log emit path flattens `severity="error"` Observations to `"warn"` at `sre.py:399-408`. Means CVE watcher fires at `warn` regardless of critical/high count. ~10-line fix; deferred from this sprint as architect deviation §3.
- **`/metrics` continuous monitoring.** Once the lab is on the public internet behind a reverse proxy, scrape `/metrics` weekly and alert if `arail_inference_wait_milliseconds{quantile="0.95"}` trends bad or `arail_security_findings{severity="critical"} > 0`. Operator runbook in `docs/PUBLISH.md` § Observability.

## Later

- **Multi-user mode** — OIDC + per-user model quotas + shared model cache.
- **Additional themes** — solar (light), inkwell (paper-on-black), CRT-amber.
- **Plugin SDK** — third-party agents and skill packs installable from a manifest URL.
- **Mobile-friendly portal** — currently desktop-first; reflow nav + cards for narrow viewports.
- **Federated lab gossip** — opt-in cross-lab knowledge sharing for trusted peers.

## Done

- Brand layer (`brand.py`) — env-driven rebrand for forks.
- Chat model gallery + Ollama send routing.
- AirLLM compatibility (deep-backend tier, both tiers).
- ARAIL rename complete (oglab → arail, 2026-04-25).

## How to use this file

- One-line entries link out to detailed plans; don't inline specs here.
- Move items between sections freely — a PR that adds an item to "Now" should
  also link the implementation plan it refers to (or create one).
- Mark `[x]` only when the item ships behind a real user-facing surface — not
  when the code merges.
