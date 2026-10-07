---
title: ARAIL v1.0.0 — First Stable Release
section: docs
audience: beginner
category: Releases
created: 2026-05-17
---

# ARAIL v1.0.0 — First Stable Release

**Released:** 2026-05-17

This is the first stable release of ARAIL. A learn-by-doing AI research
lab you can clone, set up in 12 minutes, and run on a laptop.

If you're upgrading from a pre-1.0 build, your existing `.env` keeps
working — the legacy `min`/`max` tier names auto-migrate with a
deprecation warning. Re-run `./arailctl setup` to pick up the new
`ai-eng` default model.

---

## What's new

### Two cleanly-named tiers

| Tier         | What you get                                                                                              |
|--------------|-----------------------------------------------------------------------------------------------------------|
| **Minimalist** *(default)* | Dashboard · Chat · Autoresearch · Knowledge Base · Agents · Docs · LanceDB vector recall. The everyday lab. |
| **Maximus**                | + Admin · Notebooks · QueueLLM deep-mode runtime · Anthropic SDK · LangChain · full cloud SDKs. The full bench. |

Renamed from the old `min`/`max` (and the inconsistent README typo
`minamalist`/`maximum`). Upgrade with:

```bash
./arailctl upgrade maximus
```

Downgrade by upgrading the other way — your packages stay installed,
the extra tabs just hide until you bump back up.

### One default model: `ai-eng`

`ai-eng` is a **1.5B-parameter AI engineering expert distilled from
Claude Opus 4.7** via QuKaiZen's Project Nucleus. It's the only model
that ships pre-installed.

- Lean install — no more pre-pulling 70B/405B Llama weights you may
  never use.
- The chat catalog still lists ~20 other models (Qwen, Gemma, Phi,
  DeepSeek-R1, Mistral, GPT-OSS, and more) — browse them in
  **Chat → model picker** and pull on demand with one click.
- The Compute Source pivot in Chat lets you swap in any cloud vendor
  (Claude, NVIDIA NIM, OpenRouter, HuggingFace, custom OpenAI-compatible
  endpoint) without restarting the lab.

> **Transition note:** The 1.5B self-hosted GGUF from QuKaiZen is
> still being finalized. Until it is uploaded to HuggingFace (primary)
> or the GitHub Release mirror, setup transparently uses `qwen2.5:1.5b`
> with the AI Engineer persona Modelfile as a preview base. Re-running
> `./arailctl setup` after the GGUF is uploaded picks it up
> automatically.

### QueueLLM is the Maximus deep-mode backend

When you upgrade to Maximus, ARAIL installs **QueueLLM** — our own Rust
streaming runtime — as the deep-mode backend.

- **Apple Silicon:** native, fast. QueueLLM is the default Compute
  Source pivot option once installed.
- **CUDA / Linux x86:** QueueLLM's CUDA backend is in flight; until it
  ships, Maximus on CUDA falls back to AirLLM with a clear log notice.
  Set `ARAIL_FORCE_AEROLLM=1` to disable the fallback and wait for the
  CUDA release.

### AirLLM is now opt-in

In v1.0.0 we removed AirLLM from the default install path in both
tiers. Power users who want layer-streaming 70B/405B inference can
still enable it:

```bash
ARAIL_INSTALL_AIRLLM=1 ./arailctl setup
```

Llama weights remain gated on Hugging Face — you'll need
`huggingface-cli login` (or `HF_TOKEN`) to pull them.

### Quality-of-life

- Energy cost tracking now uses `max(latency_energy, token_energy)` so
  layer-streaming backends are no longer undercounted.
- Dashboard polish — Mission Status + Activity Feed widened,
  experiments expandable, hypotheses no longer truncated.
- Docs frontmatter migrated to the unified `category` / `audience` /
  `related` schema across `lab/pkb/compiled/docs/`.

---

## Security posture (unchanged, restated)

- **`LAB_MODE=airgapped` is the default.** Cloud egress blocked; an
  audit trail of any blocked attempt is written to
  `lab/data/airgap_audit.jsonl`.
- Set `LAB_MODE=hybrid` in `.env` to allow cloud-provider chat
  requests.
- API tokens live in `lab/data/secrets.env` (`chmod 0600`,
  git-ignored, never echoed in UI or logs).

---

## Upgrade guide

### From a pre-1.0 install

```bash
git pull
./arailctl setup        # picks up new defaults; preserves your tier choice
./arailctl restart      # apply the new nav
```

What happens:

- Your `LAB_TIER=min` or `LAB_TIER=max` in `.env` is auto-migrated to
  `minimalist` / `maximus`. You'll see one deprecation warning per
  process; update your `.env` at your leisure (the shim is removed in
  v1.1.0).
- The new `ai-eng` model gets created in Ollama. The old `ai-engineer`
  Ollama model is left in place — remove it manually when you're
  confident the new one works:

  ```bash
  ollama rm ai-engineer
  ```

- AirLLM is no longer installed by default. If you depended on it,
  re-run setup with `ARAIL_INSTALL_AIRLLM=1`.

### From scratch

```bash
git clone https://github.com/qukaizen/arail.git
cd arail
./arailctl setup
./arailctl start
```

Then open <http://127.0.0.1:8080>. The lab will ask you to set a
passphrase on first load — that's the only secret you need to manage.

---

## Known limitations

- **QueueLLM CUDA backend** — not yet shipped. CUDA Maximus hosts fall
  back to AirLLM (with a clear notice) until QueueLLM CUDA lands.
- **ai-eng 1.5B self-hosted GGUF** — not yet uploaded at release time.
  Setup uses `qwen2.5:1.5b` as the preview base in the interim. Re-run
  setup once the GGUF is uploaded to HuggingFace
  (`hf.co/qukaizen/ai-eng-1.5b-gguf`) or the GitHub Release mirror to
  swap to the real 1.5B model automatically.
- **`/ready` and `/version` endpoints** — not implemented. `/health`
  and `/api/system/health` cover the diagnostic surface for now.

See [CHANGELOG.md](../CHANGELOG.md) for the full list of changes,
removals, and the v1.1.0 deprecation timeline.

---

## Thank you

ARAIL exists because people gave it real workloads to chew on — first
the design study in `research/aerollm/`, then the early lab builds
that bled into Project Nucleus, then every friend who cloned it and
asked "wait, what does this tab do?" v1.0.0 is the version where the
answer to that question is short enough to fit on a card.

If you ship something interesting with it — a new agent, a fork
rebranded for your domain, a benchmark, a war story — we want to hear
about it. File an issue, open a PR, or just drop a line.

Now go run an experiment.
