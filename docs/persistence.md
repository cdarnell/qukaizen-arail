---
title: Lab persistence
description: "Daemon mode, model warmth, auto-resume, and what survives a crash, a terminal close, or a reboot."
category: Reference
order: 7
tags:
  - persistence
  - launchd
  - models
  - research
audience: operator
related:
  - conversation-memory
  - the-lab
---

# Lab persistence

The lab is designed to *be and feel* persistent: services survive the terminal,
crashes, and reboots; models stay warm; interrupted work resumes; nothing that
looks durable silently resets.

## Daemon mode (launchd)

```
./arailctl install-daemon      # supervise: start at login + respawn on crash
./arailctl uninstall-daemon    # back to dev/foreground mode
```

Installs LaunchAgents `io.arail.portal`, `io.arail.memory` (and `io.arail.mlx`
when `MODEL_BACKEND=mlx`) rendered from `scripts/launchd/`. Properties:
`RunAtLoad` (login autostart), `KeepAlive.SuccessfulExit=false` (crashes
respawn after a 15s throttle; a deliberate stop stays stopped), logs at
`lab/logs/<svc>.{out,err}.log` (rotated >10MB at each (re)load). The plists
contain **no secrets** — the portal reads `.env` itself from the repo root.

In daemon mode `arailctl start|stop|restart|status` drive launchctl;
`scripts/start.sh` refuses to run (and the installer refuses over a running
foreground lab) so the two modes can never fight over ports.
ttyd/jupyter/code-server intentionally stay foreground-only: they hold no lab
state and auto-respawning browser-reachable shells at login is attack surface.

## Model warmth (truthful)

| Tier | Mechanism | Status you'll see |
|---|---|---|
| 0 (resident) | every Ollama request carries `keep_alive` (default **2h**, `ARAIL_OLLAMA_KEEP_ALIVE`); a real 1-token warm runs at boot (`ARAIL_TIER0_BOOT_WARM`) | `healthy — resident` vs `cold — server up, model not loaded` (from `/api/ps`, not optimism) |
| 1 (deep, QueueLLM) | background preload (`ARAIL_AEROLLM_PRELOAD`, default on) strictly gated by `background_safe()`: operator absent, Metal pressure < 0.60, jobs not halted; re-checked after acquiring the inference slot | `cold → warming → healthy(resident)` in the statusbar/switcher |

`keep_alive=-1` pins Tier 0 forever — note it raises baseline Metal pressure
and can keep the Tier 1 preload standing down. `/metrics` exposes
`arail_model_resident{tier,entry_id}` and `arail_model_warming`.

## Auto-resume research

The researcher persists its run state (`lab/data/goals/run_state.json`) on
every transition. On boot:

- interrupted run + lab not halted → **auto-resume from the last checkpoint**
  (≥0.3 progress reloads experiments from `lab/data/experiments/` and skips
  completed ones; below 0.3 it announces an honest re-plan);
- interrupted run + lab **halted** → marked `interrupted`, resume from the
  dashboard (the halt flag itself persists in `lab/data/halt.json` — a halted
  lab stays halted across restarts);
- stale `running` snapshots in `agent_workflows.json` sweep to `interrupted`.

## Chat conversations

Per [conversation-memory](conversation-memory.md): event-log transcripts under
`lab/pkb/conversations/<cid>/` — inside the PKB so *wipe the PKB = forget me*
stays true. The Chat tab restores the last conversation on open (localStorage
holds only the pointer); turns cut off by a restart are marked *interrupted*
with the partial reply preserved by the boot sweep.

## What survives what

| Event | Survives |
|---|---|
| Browser reload | chat conversation, all tabs' state (server-side) |
| Portal crash/restart | everything above + goals, run-state (auto-resume), halt flag, registry bindings, build ledger, costs incl. history, activity tail, warm Ollama model (daemon keep_alive) |
| Terminal close / logout / reboot (daemon mode) | all services return via launchd; Tier 0 re-warms at boot; Tier 1 preloads when safe |
| Nucleus restart mid-build | arail marks the run `lost` with the reason — never a frozen stale phase |
