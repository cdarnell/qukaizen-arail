---
title: Setup Arail
id: setup-arail
name: Setup Arail
domain: onboarding
version: 1.0.0
tags: [skill, setup, onboarding, install, vibe-integrate]
when_to_use:
  - When a user has just cloned the repo and run `./arailctl setup` for the first time
  - When an agent needs to walk an operator from zero to a working lab
  - When repairing a broken install or porting to a new machine
when_not_to_use:
  - When the user is already past first boot and the lab is healthy
  - When the question is about a specific subsystem (use the matching skill)
  - When the user wants to fork the blueprint into a different product
---

# Setup Arail

Procedural knowledge for taking a fresh clone and a stranger's machine and
arriving at the production build the owner intended. The job is not to
*explain* setup — `INSTALL.md` and `vibe-integrate.md` already do that. The
job is to *prompt your way there*: ask the right questions in the right
order, pick safe defaults, and stop asking the moment the lab is usable.

This skill is loaded by Buddy (and any onboarding agent) during first-run.

## The intent the owner shipped

Arail's owner shipped a lab that:

- **Defaults to airgapped.** No cloud calls until the user explicitly opts in.
- **Picks the smallest stable tier.** The user can grow into Maximus later;
  starting too big strands them on a model that won't load.
- **Names the lab after the user.** Personality agents reference the lab name
  in conversation; an unnamed lab feels rented.
- **Knows the user's first goal.** Every agent loads the goal into context.
  Without it, agents drift toward generic small-talk.

Setup is the act of getting all four of these answered without making the
user fill out a form.

## The conversation shape

Aim for **five exchanges, max**. Anything more and the user is filling in a
survey instead of opening a lab.

1. **Greeting + machine sniff.** Run `arailctl doctor` silently. Greet the
   user and tell them what you saw — chip family, accelerator, RAM, free
   disk. Confirm those numbers in a sentence.
2. **Tier choice.** Two options:
   - **Minimalist** — Python runtime + AirLLM. Lightweight inference only.
     Right when the box has ≤ 16 GB unified memory or a slow disk, or when
     the user just wants to try it.
   - **Maximus** — Python + Rust + QueueLLM. Full lab, full pipeline.
     Right when the box can spare 50 GB of disk and the user wants to run
     experiments.
   Default to Minimalist unless the doctor reading + stated goal scream
   for the bigger build. Confirm in one sentence.
3. **Lab name + first goal.** Two questions in one prompt. Accept very
   short answers. If the user says "skip" or "later," seed defaults
   (`<hostname>-lab`, "explore the lab") and move on.
4. **Privacy posture.** One question: "Is this box airgapped, or do you
   want to wire in a cloud provider for tougher questions?" Default
   airgapped. If the user asks for hybrid, persist `LAB_MODE=hybrid`
   in `.env` but **do not** ask for tokens here — that's a separate
   flow inside the portal.
5. **Hand off.** Tell the user the portal is starting, give them the
   URL (`http://127.0.0.1:8080`), and remind them they can re-run
   setup or `arailctl doctor` any time.

## What the agent must check before each prompt

- **Has setup already run?** If `lab.conf` exists and looks valid, do not
  re-prompt. Ask the user whether they want to repair, reconfigure, or
  abort.
- **Is the model already pulled?** If a starter model lives in
  `lab/data/models/`, skip the model-pull step. Mention it offhand
  ("starter model already on disk, good") and move on.
- **Did the doctor flag anything red?** If yes, stop the onboarding
  conversation and surface the failure. Do not paper over a missing
  Python, missing Rust (for Maximus), or full disk by guessing.

## Defaults the agent should hold to

| Question                | Default                  | When to override                                     |
| ----------------------- | ------------------------ | ---------------------------------------------------- |
| Tier                    | Minimalist               | Doctor reports ≥ 32 GB RAM and ≥ 50 GB free disk     |
| Starter model           | `Qwen3-8B` 4-bit         | Apple Silicon with 8 GB unified → fall to a 3B model |
| Lab name                | `<hostname>-lab`         | User offers anything, even one word                  |
| First goal              | `"explore the lab"`      | User states a concrete goal                          |
| Privacy mode            | `airgapped`              | User explicitly asks for hybrid                      |
| Buddy Tunnel            | off                      | Never auto-enable; needs gateway + internet          |

## What the agent must not do

- **Do not ask for cloud tokens during setup.** Persist `LAB_MODE` only.
  Token entry happens later, inside the portal's Manage Providers modal,
  with the airgapped guard in place.
- **Do not auto-pull a model larger than the box can hold.** Read the
  doctor's RAM and VRAM lines, and refuse oversize starter models with
  a one-line explanation.
- **Do not enable Buddy Tunnel.** That feature requires `LAB_MODE=hybrid`,
  a gateway, and per-channel credential entry — none of which are part
  of first-run setup. See [docs/BUDDY.md](../../../../docs/BUDDY.md).
- **Do not re-run destructive steps silently.** If the user appears to
  already have a working lab, ask before overwriting `lab.conf`.

## Failure modes to recognize

- **Disk full mid-pull.** Stop, surface the size delta, suggest the
  smaller tier or a clean-up command. Never resume from a half-pulled
  model.
- **Doctor reports no accelerator.** Default to CPU + GGUF; warn the
  user that local inference will be slow and ask whether they still
  want the lab on this box.
- **User picked Maximus on a small box.** Walk back to Minimalist with
  a one-sentence explanation. Do not silently downgrade.
- **HF gated model.** Stop and surface the link to the model card.
  Setup does not handle authentication for gated artifacts.

## Hand-off checklist

Before declaring setup complete, verify:

- [ ] `lab.conf` has lab name, owner-stated goal, tier, privacy mode.
- [ ] `.env` has `LAB_MODE=` set explicitly (airgapped or hybrid).
- [ ] Starter model exists on disk.
- [ ] `arailctl doctor` returns clean.
- [ ] Portal is reachable on `127.0.0.1:8080`.

If any item fails, do not declare success. Tell the user what's missing
and what their next move is.

## Related skills and docs

- [observe-lab](../observe-lab/SKILL.md) — what to surface after setup
  completes.
- [docs/INSTALL.md](../../../../docs/INSTALL.md) — the imperative form
  of this same flow, for users who want to follow steps themselves.
- [docs/vibe-integrate.md](../../../../docs/vibe-integrate.md) — the
  judgment layer for porting setup to a non-blessed machine.
- [docs/BUDDY.md](../../../../docs/BUDDY.md) — what Buddy Tunnel is
  and why it does not get enabled here.
