"""Model spec-sheet registry.

When a user points ``AIRLLM_MODEL`` (or ``AEROLLM_MODEL``) at a
HuggingFace repo, the dashboard's Frontier chip should tell them *why*
that model matters:
what it's good at, how big it is, where it sits against other open
and closed models on the benchmarks people care about.

This file is the registry. Keys are substrings matched case-
insensitively against the configured model name (so `Qwen3-235B-A22B`
matches `qwen3-235b` and also a more specific `qwen3-235b-a22b` entry
if we ship one). Add new entries here; they show up in the chip
hover without a restart (the /api/chat/models endpoint re-reads it
on every call).

The numbers here are **illustrative community snapshots**, not
authoritative benchmarks — they'll drift as leaderboards update and
as model revisions ship. Treat them as "approximately where this
model sits" rather than "exact scores on a fixed eval." If you care
about precision for a specific study, write the real numbers to the
appropriate SKILL.md or to a fresh entry here.

Schema per model:

    {
        "params":      e.g. "235B MoE (22B active per token)",
        "license":     e.g. "Apache 2.0",
        "context":     e.g. "128K tokens",
        "strengths":   list of short phrases
        "benchmarks":  list of (bench, score, compared_to) tuples
        "notes":       free-form one-paragraph summary
        "source":      URL to the model card / blog post
    }
"""

from __future__ import annotations

import json as _json
import re as _re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple


# Ordered most-specific first so the lookup pass picks the tightest
# match. Case-insensitive substring match on the deep-tier model name
# (AIRLLM_MODEL today; AEROLLM_MODEL once the Rust runtime is stable).
_SPECS: List[Tuple[str, Dict[str, Any]]] = [
    # ── Qwen3 family (Alibaba / Qwen team, Apache 2.0) ────────────────
    ("Qwen3-235B-A22B", {
        "params": "235B MoE (22B active per token)",
        "license": "Apache 2.0",
        "context": "128K tokens",
        "strengths": [
            "General reasoning with a cost-efficient MoE routing",
            "Strong multilingual (29+ languages)",
            "Tool use + function calling native",
            "Arail's default deep model",
        ],
        "benchmarks": [
            ("MMLU", "~83", "GPT-4o-mini ~82 · Llama-3.1-405B ~85"),
            ("HumanEval", "~86", "Llama-3.1-405B ~89 · DeepSeek-V3 ~89"),
            ("MATH", "~85", "GPT-4o ~76 · DeepSeek-V3 ~90"),
        ],
        "notes": (
            "Flagship Qwen3 MoE. Only ~22B params are active per token, "
            "so inference cost is closer to a 22B dense model than the "
            "total 235B. The big win is capability density per active "
            "parameter — cheaper to serve than a comparable dense model."
        ),
        "source": "https://qwenlm.github.io/blog/qwen3/",
    }),
    ("Qwen3-8B", {
        "params": "8B dense",
        "license": "Apache 2.0",
        "context": "128K tokens",
        "strengths": [
            "Fits in 16 GB RAM at 4-bit",
            "Fast enough for interactive chat on a laptop",
            "Tool use + strong instruction-following",
            "Arail's default fast model",
        ],
        "benchmarks": [
            ("MMLU", "~70", "Llama-3.1-8B ~68 · Mistral-7B ~62"),
            ("HumanEval", "~70", "Llama-3.1-8B ~67"),
            ("MATH", "~54", "Llama-3.1-8B ~47"),
        ],
        "notes": (
            "The workhorse — what you're chatting with today unless you "
            "toggle Deep. Punches well above its weight class on math "
            "and code because Qwen trained heavily on both."
        ),
        "source": "https://qwenlm.github.io/blog/qwen3/",
    }),

    # ── GLM family (Zhipu AI / Tsinghua, Apache 2.0 style) ────────────
    ("GLM-4.6", {
        "params": "~357B (MoE)",
        "license": "Open-weight (MIT-like)",
        "context": "128K tokens",
        "strengths": [
            "Code generation — near frontier proprietary on HumanEval",
            "Declarative, structured reasoning",
            "Agent workflows — strong tool-use + planning",
            "Long-context retrieval",
        ],
        "benchmarks": [
            ("HumanEval",   "~91",  "GPT-4o ~90 · DeepSeek-V3 ~89 · Claude 3.5 ~93"),
            ("MMLU",        "~83",  "Llama-3.1-405B ~85 · GPT-4o-mini ~82"),
            ("SWE-Bench",   "~35",  "GPT-4o ~33 · Claude 3.5 ~49"),
        ],
        "notes": (
            "Zhipu's flagship code + agent model. Strives to be "
            "declarative: clean output structure, follows schema well, "
            "amenable to function-call pipelines. A good pick when you "
            "want a serious coding collaborator without a cloud bill."
        ),
        "source": "https://huggingface.co/THUDM/GLM-4.6",
    }),
    ("GLM-5", {
        # Also matches GLM-5.1 via substring — see the notes.
        "params": "Varies (check repo — 32B/235B/754B variants)",
        "license": "Open-weight (MIT-like)",
        "context": "128K+ tokens",
        "strengths": [
            "Code generation — flagship-tier on HumanEval + SWE-Bench",
            "Declarative reasoning",
            "Strong chain-of-thought + planning",
            "Open-weight — runs fully local via the deep-tier backend (AirLLM)",
        ],
        "benchmarks": [
            ("HumanEval",   "high", "beats most open competitors including GPT-OSS"),
            ("SWE-Bench",   "competitive", "close to frontier proprietary on agent tasks"),
            ("MMLU",        "high", "flagship-tier among open models"),
        ],
        "notes": (
            "Zhipu AI's 2025+ flagship line. The bigger variants "
            "(~754B MoE) aim squarely at GPT-4-class capability while "
            "staying open-weight. Fits on any machine with enough disk "
            "via AirLLM layer streaming (max tier) — the speed is tokens-per-minute "
            "at that scale, but the model itself is frontier class. "
            "Edit this entry in src/arail/model_specs.py with the "
            "precise benchmark scores when you find them."
        ),
        "source": "https://huggingface.co/THUDM",
    }),

    # ── Llama family (Meta, custom license) ───────────────────────────
    ("Llama-3.1-405B", {
        "params": "405B dense",
        "license": "Llama Community (commercial OK with caveats)",
        "context": "128K tokens",
        "strengths": [
            "Strong general reasoning",
            "Broad knowledge from a long pre-training run",
            "Well-tuned for instruction following",
        ],
        "benchmarks": [
            ("MMLU",      "~85",  "GPT-4o ~88 · Claude 3.5 ~88"),
            ("HumanEval", "~89",  "DeepSeek-V3 ~89 · GLM-4.6 ~91"),
            ("MATH",      "~73",  "GPT-4o ~76 · Qwen3-235B ~85"),
        ],
        "notes": (
            "Meta's flagship open. Dense (not MoE), so inference is "
            "expensive per token — meaningful on AirLLM at ~1 TB disk "
            "for a 4-bit quant. Strong generalist; newer MoE models "
            "match or exceed it on many benchmarks at lower cost."
        ),
        "source": "https://huggingface.co/meta-llama/Llama-3.1-405B",
    }),
    ("Llama-3.1-70B", {
        "params": "70B dense",
        "license": "Llama Community",
        "context": "128K tokens",
        "strengths": [
            "Solid reasoning without the 405B's memory cost",
            "Widely supported across tooling",
        ],
        "benchmarks": [
            ("MMLU",      "~82",  "Mistral-Large ~84 · Qwen3-235B ~83"),
            ("HumanEval", "~80",  "GLM-4.6 ~91 · DeepSeek-V3 ~89"),
        ],
        "notes": "Popular local-inference sweet spot — 70B dense, widely fine-tuned, good fallback when MoE models won't fit your disk.",
        "source": "https://huggingface.co/meta-llama/Llama-3.1-70B",
    }),

    # ── DeepSeek family (DeepSeek, MIT) ───────────────────────────────
    ("DeepSeek-V3", {
        "params": "671B MoE (37B active)",
        "license": "MIT",
        "context": "128K tokens",
        "strengths": [
            "Coding + math at frontier proprietary levels",
            "Extremely cost-efficient MoE routing",
            "Strong at chain-of-thought / agentic tasks",
        ],
        "benchmarks": [
            ("HumanEval", "~89",  "GPT-4o ~90 · Claude 3.5 ~93"),
            ("MATH",      "~90",  "GPT-4o ~76 · Qwen3-235B ~85"),
            ("MMLU",      "~88",  "Llama-3.1-405B ~85 · GPT-4o ~88"),
        ],
        "notes": "One of the most capable open-weight models ever shipped. 37B active params, so inference is MoE-cheap despite 671B total.",
        "source": "https://huggingface.co/deepseek-ai/DeepSeek-V3",
    }),
    ("DeepSeek-R1", {
        "params": "671B MoE (R1: reasoning-tuned)",
        "license": "MIT",
        "context": "128K tokens",
        "strengths": [
            "Chain-of-thought reasoning — rivals o1 on hard benchmarks",
            "Shows its thinking — transparent scratchpad output",
            "Math + code + scientific reasoning",
        ],
        "benchmarks": [
            ("AIME 2024", "~80",  "o1 ~83 · Claude 3.5 ~16"),
            ("MATH-500",  "~97",  "o1 ~96 · GPT-4o ~76"),
            ("SWE-Bench", "~49",  "Claude 3.5 ~49 · GPT-4o ~33"),
        ],
        "notes": "The R1 variant is reasoning-tuned with verifiable-reward RL. Incredible at tasks that reward showing work; less snappy for casual chat.",
        "source": "https://huggingface.co/deepseek-ai/DeepSeek-R1",
    }),
]


@lru_cache(maxsize=512)
def context_tokens(label: "str | int | None") -> "Optional[int]":
    """Parse a context-window label into a token count.

    Supported formats:
      "128K tokens" → 131072
      "1M tokens"   → 1048576
      "32k"         → 32768
      "4096"        → 4096
      4096          → 4096
      None / ""     → None
      unparseable   → None

    K = 1024, M = 1024² (binary — matches llama.cpp n_ctx semantics).
    Pure function; @lru_cache so repeated lookups are O(1).
    Never raises — returns None on any parse failure.
    """
    if label is None:
        return None
    if isinstance(label, int):
        return label if label > 0 else None
    raw = str(label).strip()
    if not raw:
        return None
    # Strip trailing descriptive words, commas, etc.
    cleaned = _re.sub(r"[,\s]+(tokens?|context|window).*$", "", raw, flags=_re.IGNORECASE).strip()
    # Match a number + optional unit
    m = _re.match(r"^(\d+(?:\.\d+)?)\s*([KkMm]?)$", cleaned)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "K":
        return int(val * 1024)
    if unit == "M":
        return int(val * 1024 * 1024)
    # No unit — bare integer-ish
    return int(val) if val > 0 else None


def context_label(model_name: str) -> "Optional[str]":
    """Return the 'context' string from the model registry for *model_name*.

    Returns None when the model is not in the registry or has no context field.
    Convenience wrapper used by _resolve_ctx_override to fall back to spec data.
    """
    spec = lookup(model_name)
    if spec is None:
        return None
    return spec.get("context") or None


def lookup(model_name: str) -> Optional[Dict[str, Any]]:
    """Return the spec sheet for the given model name, or None.

    Match is case-insensitive substring; first match (most specific
    first in ``_SPECS``) wins. Returns a copy so callers can mutate
    without poisoning the registry.
    """
    if not model_name:
        return None
    needle = model_name.lower()
    for key, spec in _SPECS:
        if key.lower() in needle:
            return dict(spec)
    return None


def known_models() -> List[str]:
    """Return every registered model key — useful for docs + tests."""
    return [key for key, _ in _SPECS]


# ── Hardware-floor metadata overrides ─────────────────────────────────────
# Manual overrides for models whose name doesn't expose TOTAL param count.
# Needed for MoE models where the name reflects active-per-token params
# (e.g. "17B" in Llama-4-Maverick-17B-128E) rather than total params (~400B).
#
# Order = most-specific first. First regex match wins.
# total_params_b is in BILLIONS (400.0 = 400B).
# DO NOT add a generic "Llama-4" pattern — it would shadow specific variants.
MODEL_METADATA_OVERRIDES: List[tuple] = [
    # Persona-named Ollama tags carry no digit in their name, so the
    # name-regex fallback can't see their size — without an explicit entry
    # here they'd hit "unknown" and get refused by the answering-model
    # ceiling (arail.registry.ceiling), including the shipped default.
    # Keep these in sync with models/ai-eng/Modelfile.* `FROM` lines and
    # pyproject.toml [tool.arail.models].
    (_re.compile(r"^llama-ai-eng(?::|$)", _re.IGNORECASE), {
        "total_params_b": 1.0,
        "notes": "Persona wrap over Llama-3.2-1B-Instruct (models/ai-eng/Modelfile.default).",
    }),
    # "ai-eng" and "ai-engineer" are two DIFFERENT models that happen to
    # share a name prefix — not a rename of each other. ai-engineer was
    # ARAIL's original (pre-two-tier) default, based on qwen3:8b then
    # qwen2.5:7b; it was repositioned as the maximus deep persona when the
    # 1B llama-ai-eng/ai-eng lineage launched. Do not merge these entries or
    # treat one as a fallback alias for the other — see the corrected
    # history of _resilient_chat_default() in src/arail/portal/app.py.
    (_re.compile(r"^ai-eng(?::|$)", _re.IGNORECASE), {
        "total_params_b": 1.0,
        "notes": "v1.0.0 name for llama-ai-eng.",
    }),
    (_re.compile(r"^ai-engineer(?::|$)", _re.IGNORECASE), {
        "total_params_b": 7.0,
        "notes": "Maximus deep persona wrap over Qwen2.5-7B-Instruct (models/ai-eng/Modelfile.deep). NOT a rename of ai-eng/llama-ai-eng.",
    }),
    (_re.compile(r"Llama-4.*Maverick.*17B.*128E", _re.IGNORECASE), {
        "total_params_b": 400.0,
        "active_params_b": 17.0,
        "moe": True,
        "experts": 128,
        "license": "Llama Community",
        "context": "1M tokens",
        "notes": (
            "Llama-4 Maverick: 128-expert MoE, 17B active per token, "
            "~400B total. Streams via AirLLM (max tier)."
        ),
        "source": "https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct",
    }),
    # Llama-4 Behemoth placeholder — add when weights are released.
    # (_re.compile(r"Llama-4.*Behemoth.*288B", _re.IGNORECASE), {
    #     "total_params_b": 2000.0,
    #     "active_params_b": 288.0,
    #     ...
    # }),
]

# Hardware floor for ARAIL deployment — TOTAL parameter cap for in-GPU
# residency on the target fleet (5090 24GB / M5 36GB). Anything above
# this TOTAL must stream via a deep backend (AeroLLM / AirLLM).
# Lowered from 35.0 → 30.0 in sprint 2026-05-10-chat-model-sync to
# match actual fleet headroom and pick up deepseek-r1:32b as streamable.
HARDWARE_FLOOR_TOTAL_B = 30.0

# Headline "frontier" threshold — distinct from the OOM floor above.
# The 30B floor is a *hardware* fact (what fits in GPU); 70B is the
# *product* line where a model is framed as a frontier model that
# defaults to the 2nd inference (aeroLLM) and surfaces the chat
# comparison view. Everything 30B–70B still streams (floor wins for
# OOM safety) but isn't branded "frontier". See is_frontier().
FRONTIER_HEADLINE_B = 70.0


@lru_cache(maxsize=512)
def get_total_params(model_name: str) -> Optional[float]:
    """Return TOTAL params in billions, or None if unknown.

    Consults MODEL_METADATA_OVERRIDES first. Returns None when no override
    matches — callers must fall back to the regex-based hint path in app.py.

    Postcondition: result is LRU-cached per process (eviction at 512 entries,
    far above realistic model-name cardinality). None on empty / bad input.
    """
    if not model_name:
        return None
    for pat, meta in MODEL_METADATA_OVERRIDES:
        if pat.search(model_name):
            value = meta.get("total_params_b")
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
    return None


# ── Param resolution from an on-disk model file ────────────────────────────
# Used by the answering-model ceiling (arail.registry.ceiling) so an opaquely
# named file can't dodge the gate the way it can dodge the name-regex parser.
# Best-effort: safetensors headers are always honest (they list every tensor's
# shape); GGUF only sometimes carries an explicit parameter_count metadata key
# — when it doesn't, this returns None and the caller falls through to the
# next source in the resolution order, never inventing a number.

def params_b_from_safetensors(path: str) -> Optional[float]:
    """Sum every tensor's element count from a .safetensors header.

    The header is a length-prefixed JSON blob at the start of the file — no
    need to read the (potentially huge) tensor data itself.
    """
    try:
        with open(path, "rb") as f:
            header_len = int.from_bytes(f.read(8), "little")
            if header_len <= 0 or header_len > 100_000_000:
                return None
            header = _json.loads(f.read(header_len))
    except Exception:  # noqa: BLE001
        return None
    total = 0
    for key, meta in header.items():
        if key == "__metadata__" or not isinstance(meta, dict):
            continue
        shape = meta.get("shape")
        if not shape:
            continue
        count = 1
        for dim in shape:
            count *= int(dim)
        total += count
    if total <= 0:
        return None
    return total / 1e9


def params_b_from_gguf(path: str) -> Optional[float]:
    """Read the explicit ``general.parameter_count`` key from a GGUF header.

    Not every GGUF file carries this key. When it's absent this returns
    None rather than estimating from tensor shapes (GGUF's quantized tensor
    layout makes a cheap, reliable shape-based count impractical here) — the
    caller falls through to the catalog / name-regex sources.
    """
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None
            version = int.from_bytes(f.read(4), "little")
            if version < 2:
                return None
            _tensor_count = int.from_bytes(f.read(8), "little")
            kv_count = int.from_bytes(f.read(8), "little")

            def _read_str() -> str:
                n = int.from_bytes(f.read(8), "little")
                return f.read(n).decode("utf-8", "replace")

            _GGUF_TYPE_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

            def _skip_value(vtype: int) -> Optional[int]:
                if vtype == 8:  # string
                    return None if _read_str() is not None else None
                if vtype == 9:  # array
                    item_type = int.from_bytes(f.read(4), "little")
                    n = int.from_bytes(f.read(8), "little")
                    for _ in range(n):
                        _skip_value(item_type)
                    return None
                size = _GGUF_TYPE_SIZES.get(vtype)
                if size is None:
                    return None
                f.read(size)
                return None

            for _ in range(min(kv_count, 10_000)):
                key = _read_str()
                vtype = int.from_bytes(f.read(4), "little")
                if key == "general.parameter_count" and vtype in (10, 11, 12):
                    size = _GGUF_TYPE_SIZES[vtype]
                    raw = f.read(size)
                    value = int.from_bytes(raw, "little")
                    return value / 1e9
                _skip_value(vtype)
    except Exception:  # noqa: BLE001
        return None
    return None


def params_b_from_path(path: str) -> Optional[float]:
    """Dispatch to the right on-disk metadata reader by extension."""
    lower = path.lower()
    if lower.endswith(".safetensors"):
        return params_b_from_safetensors(path)
    if lower.endswith(".gguf"):
        return params_b_from_gguf(path)
    return None


def resolve_params_b(
    model_name: str, model_path: Optional[str] = None
) -> Tuple[Optional[float], str]:
    """Resolve total params (billions) for the answering-model ceiling.

    Resolution order — the one the review flagged as missing:
      1. on-disk metadata (safetensors tensor shapes / GGUF parameter_count)
      2. MODEL_METADATA_OVERRIDES / catalog-style override table
      3. name-regex heuristic (last resort, not a default-to-small guess)

    Returns (value_or_None, source). ``source`` is one of
    "metadata" | "override" | "name-regex" | "unknown" — callers (the
    ceiling gate) must treat "unknown" as *unknown*, never as "small".
    """
    if model_path:
        from_metadata = params_b_from_path(model_path)
        if from_metadata is not None:
            return from_metadata, "metadata"
    from_override = get_total_params(model_name) if model_name else None
    if from_override is not None:
        return from_override, "override"
    if model_name:
        m = _re.search(r"(\d+(?:\.\d+)?)([BMK])\b", model_name, _re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = m.group(2).upper()
            if unit == "B":
                return val, "name-regex"
            if unit == "M":
                return val / 1000.0, "name-regex"
            if unit == "K":
                return val / 1_000_000.0, "name-regex"
    return None, "unknown"


@lru_cache(maxsize=512)
def must_stream(model_name: str) -> bool:
    """True iff TOTAL params > HARDWARE_FLOOR_TOTAL_B (30B).

    Single source of truth for the hard hardware-floor rule. Both
    server-side dispatch (_prepare_chat_context in app.py) and the
    chat-picker streamed-badge must consult this function. Never
    re-derive the 30B threshold elsewhere.

    Falls back to a minimal inline regex when no override matches
    (can't call _model_param_hint_value — it lives in app.py, circular).
    The inline regex is a 4-line intentional duplication; see ARCHITECTURE.md
    tech debt section for the future factoring path.

    Postcondition: O(1) per call after first lookup (lru_cache).
    Bad input: empty / None / unparseable -> False (treat as small;
    safer default — a small model dispatched to streaming is just slow;
    a large model dispatched locally is OOM).
    """
    if not model_name:
        return False
    total_b = get_total_params(model_name)
    if total_b is not None:
        return total_b > HARDWARE_FLOOR_TOTAL_B
    # Override unknown — fall back to a minimal inline regex parse.
    # Intentional duplication of the _extract_param_hint regex in app.py;
    # model_specs.py cannot import app.py (circular dependency).
    m = _re.search(r"(\d+(?:\.\d+)?)([BMK])\b", model_name, _re.IGNORECASE)
    if not m:
        return False
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "B":
        return val > HARDWARE_FLOOR_TOTAL_B
    if unit == "M":
        return val / 1000.0 > HARDWARE_FLOOR_TOTAL_B
    # K -> never above the floor
    return False


@lru_cache(maxsize=512)
def is_frontier(model_name: str) -> bool:
    """True iff TOTAL params >= FRONTIER_HEADLINE_B (70B).

    The headline "this is a frontier model → 2nd inference (aeroLLM)" check.
    Distinct from must_stream() (the 30B OOM floor): a 32B model must_stream
    but is NOT frontier. Used by the chat comparison view to brand a pick as
    frontier-scale and auto-default it to the deep backend.

    Mirrors must_stream()'s lookup: overrides first, then the inline regex.
    Bad / empty / unparseable input -> False.
    """
    if not model_name:
        return False
    total_b = get_total_params(model_name)
    if total_b is not None:
        return total_b >= FRONTIER_HEADLINE_B
    m = _re.search(r"(\d+(?:\.\d+)?)([BMK])\b", model_name, _re.IGNORECASE)
    if not m:
        return False
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "B":
        return val >= FRONTIER_HEADLINE_B
    if unit == "M":
        return val / 1000.0 >= FRONTIER_HEADLINE_B
    return False
