---
title: Blueprints
category: Design
order: 10
tags:
  - blueprints
  - design
  - philosophy
audience: beginner
related:
  - design
  - CONTRIBUTING
---
# Blueprints

A blueprint is a lab you can run. It ships with a goal (or asks for one), a
set of agents, a tier config, and default models. You spin it up in a few
minutes; from there you change whatever you want.

This file is the overview. The schema, command details, and authoring guide
live in [`blueprints/README.md`](./blueprints/README.md).

Blueprints are concrete TOML artifacts under
[`blueprints/<id>/blueprint.toml`](./blueprints/), wired through
the `./arailctl blueprint` subcommand:

```bash
./arailctl blueprint list                                # available blueprints
./arailctl blueprint catalog                             # model compatibility matrix
./arailctl blueprint create research --from autoresearch # scaffold a new instance
./arailctl blueprint apply research                      # re-render after editing
./arailctl blueprint destroy research                    # remove an instance
```

Each `arailctl blueprint create` scaffolds an isolated instance under
`instances/<name>/` (its own `.env`, `lab.conf`, `log/`, port range).
The default ARAIL lab — what `./arailctl setup` provisions at the repo
root — is untouched. See
[`blueprints/README.md`](./blueprints/README.md) for the full
schema and authoring guide.

## The principle

Here is a blueprint. Build on top of it, or replace it.

The default one is **Autoresearch**. It ships with a researcher agent, a
curator, and an experiment tracker. Give it a topic; it starts gathering,
organizing, and writing. If that's not your work, fork it. Change the agents.
Swap the models. Rewire the integrations. The Python package (`arail`) stays
the same; your lab looks different.

Rename your fork in one line of `.env`:

```bash
LAB_NAME="Sam's AI Lab"
```

Every banner, nav logo, activity line, and wiki page rebrands on restart.

## Available blueprints

The default `autoresearch` blueprint plus three concrete forks now
ship under [`blueprints/`](./blueprints/):

| Blueprint                                            | Tier | Default model | Goal                                                           |
|------------------------------------------------------|------|---------------|----------------------------------------------------------------|
| [`autoresearch`](./blueprints/autoresearch/)         | minimalist | Qwen3-8B      | Researcher + curator + experiment tracker on a topic you set   |
| [`status-digest`](./blueprints/status-digest/)       | minimalist | Qwen2.5-3B    | Monday-morning brief — what shipped, blocked, needs attention  |
| [`inbox-triager`](./blueprints/inbox-triager/)       | minimalist | Qwen2.5-7B    | Email classification + reply-drafting (consent-gated, never auto-sends) |
| [`client-followup`](./blueprints/client-followup/)   | maximus | Qwen2.5-7B    | Post-meeting follow-up + relationship cadence for consultants  |

Each blueprint directory has its own README documenting which
agents the blueprint expects (some exist today; some are
aspirational and link to where the implementation lands).

## Sharing a blueprint

Built one worth sharing? Open a pull request. Include a short readme that
says what goal it solves, which agents it ships, which install tier it
needs, and any external integrations.

## Upstream contributions

ARAIL benefits when its open-source dependencies improve. Where we
hit and fix bugs in upstream projects, we contribute the patch back:

- **AirLLM MLX-on-Apple-Silicon torch-tensor crash** —
  [`lyogavin/airllm#280`](https://github.com/lyogavin/airllm/issues/280) /
  [`#281`](https://github.com/lyogavin/airllm/pull/281). On macOS,
  AirLLM's `AutoModel.from_pretrained` routes every architecture
  through `AirLLMLlamaMlx`, which crashed with
  `ValueError: Cannot index mlx array using the given type` when fed
  the natural `tokenizer(text, return_tensors="pt").input_ids` input.
  Patch coerces input to `mlx.array` at the `generate()` boundary;
  6 regression tests included. Discovered while measuring
  the QueueLLM-vs-AirLLM headline comparison for v0.1-alpha.

  Until the patch lands upstream, install from our fork to use the
  AirLLM toggle on Apple Silicon:
  `pip install git+https://github.com/qukaizen/airllm.git@fix/mlx-torch-tensor-coerce`
