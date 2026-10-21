# Model Forge — a tour

Model Forge is ARAIL's local, in-repo distillation pipeline: name a
domain, stage a local corpus, run the build, get a signed DNA card. It
replaced the old `/build` tab (a thin client for a separate
`qukaizen-nucleus` process) on 2026-09-23 — see
`sprints/2026-09-23-nucleus-sprint-1/ARCHITECTURE.md` for the design.

Everything here is `arailctl nucleus <verb>` (the `qkz` symlink works
too, since `qkz` is `arailctl`'s own alias on this Mac — not the
separate Nucleus Rust binary of the same name; see
`~/ProJects/CLAUDE.md`'s "Naming status" section if that's confusing).

## The five verbs, in order

```
arailctl nucleus plan "<intent>" --name <slug>     # writes configs/domains/<slug>.yaml
arailctl nucleus stage <slug> --source KIND:NAME=<path> [...]
arailctl nucleus build <slug> --profile local
arailctl nucleus certify <build_id> [--publish-row]
arailctl nucleus verify <shard>@<version>|<dir> [--fast]
```

1. **`plan`** writes a `configs/domains/<slug>.yaml` template (edit it —
   the `corpus.sources` placeholders and the 10-line
   `<slug>.eyeball.txt` need real values) and, given an existing domain,
   confirms it loads and resolves the student model under
   `$ARAIL_MODELS_DIR`. There's no interview yet — a future Buddy
   `nucleus-partner` skill will call the same function this verb calls.
2. **`stage`** reads local paths you pass on the CLI — it never fetches
   anything itself, not even `git fetch`. Clone the repo (or whatever
   corpus source you're using) yourself, first, with network on; then
   run `stage` with `LAB_MODE` however you like, since `stage` itself
   makes zero network calls regardless of mode.
3. **`build --profile local`** runs preflight (memory plan, Buddy
   protection, tokenizer parity, deep-runtime capability), then five
   phases as separate subprocesses: extract (teacher, train prompts,
   top-N logprobs), extract-on-cert (teacher baseline), train (Arbitrage
   LoRA cycles, dev-only), fuse, and eval (student vs base on the cert
   set). `--resume <build_id>` picks up where a killed build left off.
4. **`certify`** runs the contamination gate first — it refuses (exit 3,
   no card written) if train/cert overlap is ≥1% or there's a temporal
   leak — then writes the DNA card, the Ed25519 seal, and the build
   report, and appends a row to a **local, untracked**
   `lab/data/nucleus/CERTIFIED_SHARDS.md` ledger. Pass `--publish-row`
   to also append to the tracked `docs/CERTIFIED_MODELS.md` (only inside
   a delimited marker section; nothing else in that file is touched).
5. **`verify`** re-checks a card's signature, key trust, and content
   hashes without trusting anything the build process claimed.

`status <build_id>` prints a build's `run.json`; `list` and `/forge`
(a read-only web viewer — no build-triggering there) show what's been
certified.

## Airgapped prerequisites

Model Forge's `build`/`certify`/`verify` steps make **zero network
calls** by design, and run correctly with `LAB_MODE=airgapped`. But
getting a corpus and a student base model onto disk in the first place
needs the network, once, before you flip to airgapped:

```bash
git clone https://github.com/torvalds/linux         # or your corpus source
hf download mlx-community/Qwen2.5-3B-Instruct-4bit --local-dir lab/models/Qwen2.5-3B-Instruct-4bit
echo 'LAB_MODE=airgapped' >> .env      # if not already set — it's the default
```

Then `stage` and `build` run with no egress, provably (worker
subprocesses set `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`/
`HF_DATASETS_OFFLINE` and install the same egress guard the rest of the
lab uses).

## Tiers

`plan`, `verify`, `status`, `list`, and the `/forge` viewer work on
**minimalist**. `stage`, `build`, `eval`, `certify`, and `spike` need
**maximus** plus the deep runtime (`./arailctl tier maximus`, then
`./arailctl deep install`).

## What's real today, what's a seam

Proven end to end against a synthetic fixture corpus, in CI, with a
deterministic stub provider standing in for the deep runtime (Gate A —
`tests/nucleus/test_e2e_gate_a.py`): the whole `plan → stage → build →
certify → verify` loop, a real signed Ed25519 seal the real Nucleus Rust
`qkz isotope verify` binary accepts, contamination gating, and the DNA
card schema.

Not yet built this sprint (see `sprints/BACKLOG.md` for the filed
follow-ups): the real MLX training loop's multi-cycle batch-reading
wiring (`build.py`'s stub path is what CI exercises; the M5 real-runtime
path needs commit 28's opt-in `unstable-api` rebuild plus the training
loop completion), the `arail-ops` MCP tools, the Buddy `nucleus-partner`
skill, and GGUF export.

## Gateway / mixed profiles

`teacher.profile: gateway` or `mixed` always refuses this sprint —
in `airgapped` with the standard airgap notice, in `hybrid` with "the
live gateway lands in nucleus-sprint-2". See
`docs/nucleus-gateway-contract.md` for the contract the eventual live
gateway must satisfy.
