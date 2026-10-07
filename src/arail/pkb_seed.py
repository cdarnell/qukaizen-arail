"""Starter-pack seeding for the PKB.

New labs face a blank slate — no sources to read, no examples of how
to format a note, nothing for the researcher or curator agents to
consult on the first run. This module ships curated starter packs
that populate ``lab/pkb/sources/seeds/<pack>/`` on first boot so the
knowledge base feels alive from minute one.

Packs are **idempotent**: once installed, calling ``seed_*`` again is
a no-op unless ``force=True``. Deleting an individual file and
restarting does NOT refill it — respects the user's intent to curate.
Users can reset the whole pack from the dashboard.

Each file is a short markdown primer with YAML frontmatter so the
wiki compiler picks it up automatically. ``source_ref:`` frontmatter
lets the wiki render a "source →" link to the original material.

Start with one pack — ``model-building`` — so the example path is
concrete. Future packs might cover research methodology, prompt
engineering, or domain-specific fields (farming, culinary, etc.).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from arail.pkb import _pkb_root

log = logging.getLogger(__name__)


def _seed_dir(pack: str, pkb_root: Path | None = None) -> Path:
    root = pkb_root or _pkb_root()
    return root / "sources" / "seeds" / pack


def list_packs(pkb_root: Path | None = None) -> list[dict[str, Any]]:
    """Return every registered pack + installed status.

    Used by /api/pkb/seeds to drive the dashboard + Knowledge page
    starter-pack button.
    """
    out = []
    for pack_id, meta in _PACKS.items():
        directory = _seed_dir(pack_id, pkb_root)
        installed = directory.exists() and any(directory.glob("*.md"))
        file_count = sum(1 for _ in directory.glob("*.md")) if installed else 0
        out.append({
            "id": pack_id,
            "title": meta["title"],
            "description": meta["description"],
            "files": file_count,
            "installed": installed,
        })
    return out


def install_pack(pack: str, *, force: bool = False,
                 pkb_root: Path | None = None) -> dict[str, Any]:
    """Install a named pack. Idempotent unless ``force=True``."""
    if pack not in _PACKS:
        return {"ok": False, "error": f"unknown pack: {pack}"}

    directory = _seed_dir(pack, pkb_root)
    directory.mkdir(parents=True, exist_ok=True)

    meta = _PACKS[pack]
    written = 0
    skipped = 0
    for filename, content in meta["files"]:
        target = directory / filename
        if target.exists() and not force:
            skipped += 1
            continue
        target.write_text(content)
        written += 1

    return {
        "ok": True,
        "pack": pack,
        "written": written,
        "skipped": skipped,
        "directory": str(directory),
    }


def remove_pack(pack: str, *, pkb_root: Path | None = None) -> dict[str, Any]:
    """Delete every .md file inside the named pack's seed dir.

    Idempotent — removing an absent or empty pack returns ``removed=0``.
    Only operates inside ``_seed_dir(pack)`` so this can never walk into
    user content. Empties the directory then removes it; sibling packs
    stay intact.
    """
    if pack not in _PACKS:
        return {"ok": False, "error": f"unknown pack: {pack}"}

    directory = _seed_dir(pack, pkb_root)
    removed = 0
    if directory.exists():
        # Only unlink files we own — *.md inside the seed dir. Anything
        # the user dropped in there manually with a different extension
        # stays put (better to be conservative).
        for md_file in directory.glob("*.md"):
            try:
                md_file.unlink()
                removed += 1
            except OSError as exc:
                log.warning("could not remove %s: %s", md_file, exc)
        # Best-effort cleanup of the now-empty dir; leave it if anything
        # else lives in it.
        try:
            directory.rmdir()
        except OSError:
            pass

    return {
        "ok": True,
        "pack": pack,
        "removed": removed,
        "directory": str(directory),
    }


def seed_all_on_startup(pkb_root: Path | None = None) -> dict[str, Any]:
    """Install every pack that should be present on a fresh lab.

    Called from the portal startup hook. Idempotent by design — only
    installs what's missing. Failures are logged but never abort
    startup; a broken seed pack shouldn't stop the portal from booting.
    """
    summary = {"installed_packs": [], "errors": []}
    for pack_id, meta in _PACKS.items():
        if not meta.get("auto_install", False):
            continue
        directory = _seed_dir(pack_id, pkb_root)
        if directory.exists() and any(directory.glob("*.md")):
            continue  # already installed, skip
        try:
            result = install_pack(pack_id, pkb_root=pkb_root)
            if result.get("ok"):
                summary["installed_packs"].append(pack_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Seed pack %s failed: %s", pack_id, e)
            summary["errors"].append(f"{pack_id}: {e}")
    return summary


# ───────────────────────────────────────────────────────────────────────
# Pack: model-building
#
# The first example pack — 9 short primers on building, running, and
# tuning local language models. Each file is self-contained: YAML
# frontmatter + a one-paragraph overview + 2-3 bullet points + a
# "source" link. Short on purpose so the wiki can render them without
# pagination and the researcher agent can read a pack in one pass.
# ───────────────────────────────────────────────────────────────────────

_MODEL_BUILDING_README = """---
title: Model-Building Starter Pack
section: seeds
tags: [seed, model-building, overview]
aliases: [starter-pack, model-building-pack]
---

# Model-Building Starter Pack

Nine primers on building, running, and tuning local language models.
Each one is a ~200-word summary with a link to the canonical source
so you (and the researcher agent) have something concrete to read
when the lab is brand new.

## What's in here

- **01** — MLX + mlx-lm (Apple Silicon native inference)
- **02** — llama.cpp + GGUF (CPU and mixed-mode inference, any OS)
- **03** — Hugging Face Hub (model cards, licensing, downloads)
- **04** — Quantization basics (4/8/16-bit tradeoffs)
- **05** — Qwen3 family (our default model series)
- **06** — AirLLM (layer-streaming for 70B+ local models; ships in both tiers)
- **07** — Prompt engineering fundamentals
- **08** — Local vs hosted inference (cost, latency, privacy)
- **09** — Choosing a base model (Instruct vs base, size, license, format)

## How to use it

Read them in order the first time. After that, they're just search
hits — when you ask the chat "what's GGUF?" the model has access to
primer 02 as context.

## Removing the pack

Delete any file you don't want. The lab won't re-install it on
restart (that would override your intent). To get the whole pack
back: **Knowledge → Install starter pack** or
`POST /api/pkb/seed` with `{"pack": "model-building", "force": true}`.

## Extending the pack

Drop new primers next to these files using the same frontmatter
shape. They'll appear in the wiki + knowledge base on the next
rebuild.
"""


_MLX_PRIMER = """---
title: MLX + mlx-lm — Apple Silicon native inference
section: seeds
tags: [seed, mlx, apple-silicon, inference]
source_ref: https://github.com/ml-explore/mlx-lm
aliases: [mlx-apple-silicon, apple-silicon-inference]
---

# MLX + mlx-lm

**MLX** is Apple's first-party array framework for Apple Silicon —
the Mac equivalent of what CUDA is for Nvidia. It's unified-memory
aware, which matters: a 32 GB M-series Mac can load models that would
need a dedicated 32 GB discrete GPU because CPU and GPU share the
same RAM pool.

**mlx-lm** is the language-model wrapper around MLX. It handles the
transformer architectures, tokenizers, and sampling (temperature,
top-p, top-k). It ships with pre-converted 4-bit quantized versions
of Qwen, Llama, Mistral, and others on the `mlx-community` Hugging
Face org.

## Why it matters here

- **Arail's default backend on Mac** — `MODEL_BACKEND=mlx` auto-detects
  Apple Silicon and loads the configured `MODEL_NAME`.
- No driver install, no CUDA toolkit, no VM. Just `pip install mlx mlx-lm`.
- Inference speed is roughly 60-80% of an Nvidia discrete GPU at 2-3x
  the memory headroom.

## How we use it

`src/arail/router/backends.py:MLXBackend` — loads the model via
`mlx_lm.load()`, generates via `mlx_lm.generate()`, and builds a
sampler with `make_sampler(temp, top_p)` when the user picks a preset
on the dashboard.

Source: <https://github.com/ml-explore/mlx-lm>
"""


_LLAMA_CPP_PRIMER = """---
title: llama.cpp + GGUF — CPU-first, run-anywhere inference
section: seeds
tags: [seed, llama-cpp, gguf, cpu, quantization]
source_ref: https://github.com/ggerganov/llama.cpp
aliases: [llama-cpp, gguf]
---

# llama.cpp + GGUF

**llama.cpp** is a C++ implementation of LLM inference that runs on
a CPU alone, or optionally offloads layers to a GPU. It's the most
portable local-inference stack — works on Intel, ARM, AMD, and
Apple Silicon with no driver install.

**GGUF** (GPT-Generated Unified Format) is its model file format:
one file holds the weights + tokenizer + metadata. Quantized
variants (Q2, Q4, Q5, Q8) trade quality for file size — an 8B model
that's 16 GB at full precision becomes 4 GB at Q4_K_M.

## Why it matters here

- **Arail's CPU fallback** — works on any Linux / Mac / Windows-WSL
  machine without a GPU.
- Model files are single `.gguf` blobs you can download from Hugging
  Face and drop into `lab/models/`.
- Slower than MLX or CUDA (~5-15 tokens/sec on a modern laptop) but
  runs anywhere.

## How we use it

`src/arail/router/backends.py:CPUBackend` wraps `llama_cpp.Llama`.
The setup script pulls a 4-bit quantized Qwen3-8B GGUF by default
when no GPU is detected.

Source: <https://github.com/ggerganov/llama.cpp>
"""


_HF_HUB_PRIMER = """---
title: Hugging Face Hub — the model distribution layer
section: seeds
tags: [seed, huggingface, models, licensing]
source_ref: https://huggingface.co/docs/hub/index
aliases: [hf-hub, huggingface-hub]
---

# Hugging Face Hub

The de-facto distribution channel for open-weight models. Every
major local-runnable model — Llama, Qwen, Mistral, DeepSeek, Gemma —
publishes its weights here first.

## What a model page tells you

- **Weights** in multiple formats (safetensors for GPU, GGUF for CPU,
  MLX for Apple Silicon).
- **License** — not all "open" models are permissive. Check for
  "commercial use" before you build on one.
- **Model card** — intended use, training data, known biases, sizes.
- **Community fine-tunes** — LoRA adapters, quantized variants, chat
  templates.

## Why it matters here

- `MODEL_NAME=Qwen/Qwen3-8B` means "pull from this HF repo."
- Setup auto-downloads the starter model on first `./arailctl setup`.
- The router's HuggingFaceBackend can also hit HF's hosted inference
  API (free tier) when `MODEL_BACKEND=huggingface`.

## Auth

Anonymous downloads are rate-limited. Set `HUGGING_FACE_HUB_TOKEN` in
`.env` for unlimited access (free account, read-only token is enough).

Source: <https://huggingface.co/docs/hub/index>
"""


_QUANTIZATION_PRIMER = """---
title: Quantization basics — 4/8/16-bit tradeoffs
section: seeds
tags: [seed, quantization, q4, q8, size-vs-quality]
source_ref: https://huggingface.co/docs/optimum/concept_guides/quantization
aliases: [quantization, q4-q8]
---

# Quantization basics

Quantization replaces each model weight (normally a 16-bit or 32-bit
float) with a lower-precision representation. The model gets
smaller and faster, at some quality cost.

## The common levels

- **FP16 / BF16** — full precision for inference. An 8B model is
  ~16 GB. No quality loss.
- **INT8 / Q8** — 8-bit integers. ~50% smaller. Near-zero quality
  loss; good default for production.
- **Q4 / Q4_K_M** — 4-bit. ~75% smaller. Noticeable quality dip on
  tasks that need precision (math, code), barely perceptible on
  chat. Most-popular format for local inference.
- **Q2 / Q3** — 2-3 bits. Only for "does it fit" experiments; quality
  drops sharply.

## Rule of thumb

- Fits in RAM at Q4? Use Q4. Save the headroom for context.
- Got 2x the RAM? Bump to Q8 for noticeably better code / math.
- Got 4x? Run full precision — you're not memory-bound.

## Why it matters here

The setup script picks a quantization level based on your hardware
(4-bit on laptops, 8-bit on workstations). You can override via
`MODEL_NAME` — e.g., pull `Qwen/Qwen3-8B-GGUF` with the `Q8_0`
variant for higher quality.

Source: <https://huggingface.co/docs/optimum/concept_guides/quantization>
"""


_QWEN3_PRIMER = """---
title: Qwen3 family — our default model series
section: seeds
tags: [seed, qwen3, alibaba, model-family]
source_ref: https://qwenlm.github.io/blog/qwen3/
aliases: [qwen3, qwen-family]
---

# Qwen3 family

Alibaba's open-weight LLM series — as of 2026 the default on Arail
because it's strong at reasoning, permissive license, and ships in
a wide size range so you can pick one that matches your hardware.

## Sizes available

| Size | Params | Fits in | Best for |
|---|---|---|---|
| Qwen3-1.5B | 1.5B | 4 GB RAM | Mobile, edge, fast drafts |
| Qwen3-8B | 8B | 8-16 GB RAM | **Default** — most laptops |
| Qwen3-32B | 32B | 24-48 GB RAM | Workstations |
| Qwen3-235B-A22B | 235B MoE | 48+ GB | Layer-streaming (AirLLM) |

## Why we default to it

- **Strong instruction-following** — holds the system prompt well.
- **Tool-use ready** — understands function-call style when you
  need it.
- **Apache 2.0 license** — commercial use allowed.
- **Qwen team publishes MLX, GGUF, and safetensors variants** — one
  model family works across all our backends.

## How it's wired

`MODEL_NAME=mlx-community/Qwen2.5-3B-Instruct-4bit` (MLX default — kept under
the primary answering-model ceiling; see `arail.registry.ceiling`).
`MODEL_NAME=Qwen/Qwen2.5-3B-Instruct` (CUDA / HuggingFace default).
`AEROLLM_MODEL=Qwen2.5-7B-Instruct-4bit` (QueueLLM secondary — larger models
are fine here, capped by discovered hardware, not by the primary ceiling).

Source: <https://qwenlm.github.io/blog/qwen3/>
"""


_AIRLLM_PRIMER = """---
title: AirLLM — layer-streaming inference for 70B+ models on small hardware
section: seeds
tags: [seed, airllm, large-models, disk-streaming]
source_ref: https://github.com/lyogavin/airllm
aliases: [airllm, layer-streaming]
---

# AirLLM

Runs Llama-3.1 70B (or 405B) on hardware that can't hold the full
weights at once. The trick: load one transformer layer at a time
from disk into memory, run the forward pass through it, evict it,
and stream in the next layer. Memory footprint is one layer plus
KV cache — typically a few GB — instead of the full weight file.

The official PyPI line says it best: *"single 4 GB GPU card to run
70B large language models without quantization, distillation or
pruning. 8 GB vmem to run 405B Llama 3.1."*

## The tradeoff

- **Memory:** small — a working set of a single layer plus KV.
- **Speed:** tokens-per-minute, not tokens-per-second. The disk
  read for each layer dominates each generated token.
- **Disk:** needs the full weight file on local storage (~140 GB
  for an unquantized 70B; ~40 GB for a 4-bit quant; ~800 GB for
  the 405B).

## When it makes sense

- **Heavy research tasks** the researcher agent runs overnight or
  during the configured heavy work window (default 22:00–08:00).
- **Frontier capability checks** where you want a real 70B / 405B
  judgment instead of an 8B approximation.
- **Reproducibility** — runs locally, no API spend, no rate limits,
  no provider drift.
- **Machines with small GPUs** where you'd otherwise be stuck with
  8B–13B models or paying for a hosted endpoint.

## Never use it for

- Interactive single-turn chat. The per-prompt latency ruins it.
- Short observations the researcher makes mid-experiment.

## Compatibility heads-up

AirLLM is sensitive to model architecture. **Llama 2 / 3 / 3.1 is
the canonical happy path.** Other families (Mistral, Qwen, DeepSeek,
GLM) work in some versions but break in others. The lab's defaults
(`airllm_min` = Llama-3.1-70B, `airllm_max` = Llama-3.1-405B) are
both Llama for this reason. Confirm against the AirLLM README
before swapping to a non-Llama model.

## How we use it

Arail keeps a fast SLM (Qwen3-8B) always loaded for interactive work
and loads AirLLM on demand for the deep-research path during the
heavy work window. The dashboard's *Deep model* toggle routes a
single chat message through it. See
`src/arail/router/backends.py:AirLLMBackend`.

Source: <https://github.com/lyogavin/airllm>
"""


_PROMPT_ENG_PRIMER = """---
title: Prompt engineering fundamentals
section: seeds
tags: [seed, prompting, system-prompt, few-shot]
source_ref: https://www.promptingguide.ai/
aliases: [prompt-engineering, prompting-basics]
---

# Prompt engineering fundamentals

Three levers to shape what a model says.

## 1. System prompt

The persistent instructions that set the model's role. Goes at the
top of every conversation. Good system prompts are:

- **Specific** — "You are a Python tutor for high-school students"
  beats "You are helpful."
- **Short** — 2-5 sentences. Longer prompts dilute attention.
- **Prescriptive about format** — "Reply in JSON with keys `name`
  and `reason`" is dramatically more reliable than "format your
  reply as JSON."

## 2. Few-shot examples

Show the model 2-3 input/output pairs before asking for one of your
own. Works better than any amount of "please" for structured output.

## 3. Temperature + top-p

The sampling knobs. Arail's dashboard presets map these:

- **Factual / Code** — temp 0.1-0.2, top-p 0.9 — answers that match
  the training distribution most closely.
- **Default** — temp 0.7, top-p off — balanced.
- **Creative** — temp 1.2, top-p 0.95 — brainstorming, variations.

## Further reading

- <https://www.promptingguide.ai/> — canonical reference.
- Arail's lab_brain module composes the system prompt for the chat
  API: `src/arail/lab_brain.py`.

Source: <https://www.promptingguide.ai/>
"""


_LOCAL_VS_HOSTED_PRIMER = """---
title: Local vs hosted inference — cost, latency, privacy
section: seeds
tags: [seed, local-inference, hosted-inference, tradeoffs]
source_ref: https://huggingface.co/blog/llm-inference
aliases: [local-vs-cloud, hosted-vs-local]
---

# Local vs hosted inference — three tradeoffs

## Cost

- **Local** — electricity (~$0.13/kWh × your GPU's wattage). A
  700W workstation running 8 hours / day = ~$0.70 / day = $21 / month.
- **Hosted** — per-token API fees. Claude Sonnet: ~$3 in / $15 out
  per million tokens. A busy chat user burns $50-200 / month.
- **Crossover** — if you're doing more than ~1M tokens / month,
  local wins. Less than that, hosted wins.

## Latency

- **Local (Apple Silicon MLX, 8B model)** — first token in 0.3s,
  ~60-80 tokens/sec. Great for interactive chat.
- **Local (CPU, 8B GGUF)** — first token in 1-2s, ~5-15 tokens/sec.
  Tolerable for chat, painful for long replies.
- **Hosted API** — first token in 0.5-1.5s, 50-100 tokens/sec with
  good network. Capped by your connection.

## Privacy

- **Local** — nothing leaves the machine. Zero data-retention risk.
- **Hosted** — whatever the provider's retention policy says.
  Anthropic deletes after 30 days unless you've opted in to
  training; OpenAI retains up to 30 days unless you've signed a
  ZDR agreement.

## Arail's stance

Airgapped mode by default — local only, zero outbound calls. Hybrid
mode enables hosted fallback with per-domain consent. The dashboard's
cost meter shows the cloud-equivalent cost of everything you run
locally so you can see the savings accumulate.

Source: <https://huggingface.co/blog/llm-inference>
"""


_CHOOSING_BASE_MODEL_PRIMER = """---
title: Choosing a base model — picking the right model is half the battle
section: seeds
tags: [seed, model-building, base-model, fine-tuning, licensing]
source_ref: https://huggingface.co/docs/transformers/main/en/model_doc/auto
aliases: [choosing-a-model, base-model-selection, which-model]
---

# Choosing a base model

Before you fine-tune, quantize, or chat, you pick a base model — and
that single choice decides more about the result than almost anything
you do afterward. A few rules of thumb save a lot of wasted downloads.

## Instruct vs. Pretrained (base)

- **Instruct / Chat** models are already tuned to follow instructions
  and hold a conversation. Start here for an assistant — a fine-tune
  only has to *add* your domain knowledge on top.
- **Pretrained (base)** models are raw next-token predictors. Choosing
  one means teaching all chat behavior from scratch — far more data and
  compute. Only pick base if you're doing a full instruction distill.

## Size: small is cheap, big is smart

- Smaller models (1–3B) load fast, sip RAM, and run on a laptop — but
  they're weak at hard reasoning and **code**, which is the first thing
  to suffer. A 1B is great for chat, shaky as an "engineering expert."
- Bigger models (7B+) reason better but cost RAM and latency. Match the
  size to the *hardest* thing you'll ask, not the average thing.

## Three traps

- **Pre-quantized variants** (names with `INT4`, `QLORA`, `SpinQuant`,
  `AWQ`, `GPTQ`) are deployment end-products. You fine-tune on
  full-precision weights and quantize *last* — don't train on these.
- **License.** Check the model card. Apache-2.0 / MIT are fully free to
  fork and redistribute; "research-only" or community licenses (Llama,
  some Qwen sizes) carry naming, attribution, or use restrictions that
  travel with anything you ship. Pick the license before the weights.
- **File format.** Tools expect Hugging Face safetensors. A vendor's
  native checkpoint (Meta `.pth`, etc.) needs a conversion step first.

## Why it matters here

ARAIL's default `ai-eng` is exactly this decision made deliberately: a
compact, permissively-licensed instruct base + a domain LoRA, packaged
as a single GGUF. Your fork makes the same call — see the chat catalog
(`src/arail/chat/models_catalog.yaml`) for the browse-and-pull gallery.

Source: <https://huggingface.co/models> (filter by task, size, license)
"""


_PACKS: dict[str, dict[str, Any]] = {
    "model-building": {
        "title": "Model-building starter pack",
        "description": "10 primers on running local language models — choosing a base, MLX, llama.cpp, Qwen3, quantization, prompting.",
        "auto_install": True,
        "files": [
            ("00-readme.md", _MODEL_BUILDING_README),
            ("01-mlx-apple-silicon.md", _MLX_PRIMER),
            ("02-llama-cpp-gguf.md", _LLAMA_CPP_PRIMER),
            ("03-huggingface-models.md", _HF_HUB_PRIMER),
            ("04-quantization-basics.md", _QUANTIZATION_PRIMER),
            ("05-qwen3-family.md", _QWEN3_PRIMER),
            ("06-airllm-layer-streaming.md", _AIRLLM_PRIMER),
            ("07-prompt-engineering.md", _PROMPT_ENG_PRIMER),
            ("08-local-vs-hosted.md", _LOCAL_VS_HOSTED_PRIMER),
            ("09-choosing-a-base-model.md", _CHOOSING_BASE_MODEL_PRIMER),
        ],
    },
}
