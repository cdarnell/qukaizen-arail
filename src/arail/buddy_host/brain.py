"""The edge's view of the brain: which model answers a phone question, and may it.

ARCHITECTURE.md §5.5, §5.6. Agent ``buddy`` may use exactly one brain: the registry's
``fast`` profile, and only while that entry is a **local** ``ollama_native`` model
reached over loopback. A cloud entry, a remote endpoint, any other backend, or no
entry at all is refused (``brain_not_local`` / ``brain_unavailable``). There is no
fallback: ``fast`` has none in the registry, and the edge adds none.

This module only decides and describes. The functions marked *seam* are the three
places the edge touches the outside world (registry, Ollama, the model catalog); tests
replace them.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from arail.buddy_host import CONTRACT
from arail.buddy_host.admission import EdgeError

MODEL_NAME = "buddy-fast"
HEADER_PREFIX = CONTRACT["turn_header"]["prefix"]
_LIM = CONTRACT["limits"]["brain_chat"]
TOTAL_CHARS_MAX = int(_LIM["total_chars_max"])
MAX_TOKENS_MAX = int(_LIM["max_tokens_max"])
RESPONSE_SCHEMA_BYTES_MAX = int(_LIM["response_schema_bytes_max"])
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
NON_LOCAL_PROVIDERS = frozenset({"aerollm", "anthropic", "xai", "gateway"})
MIN_OLLAMA = (0, 5)
_PROBE_OK_TTL_S = 60.0
_PROBE_FAIL_TTL_S = 10.0

_probe_cache: dict[str, tuple[float, bool]] = {}
_probe_lock = threading.Lock()


@dataclass(frozen=True)
class BrainView:
    available: bool
    local: bool
    model_id: Optional[str]
    backend: Optional[str]
    structured_output: bool
    attribution: Optional[str]
    reason: Optional[str]
    entry: Any = None  # the ModelEntry; never serialised

    def public(self) -> dict:
        return {"available": self.available, "local": self.local, "model_id": self.model_id,
                "backend": self.backend, "structured_output": self.structured_output,
                "attribution": self.attribution, "reason": self.reason}


# --------------------------------------------------------------------------- seams
def resolve_entry() -> Any:
    """The registry entry for profile ``fast`` on the agents tab, or None. (seam)

    Uses ``peek``: the handshake asks every 20 s and must not write a fallback event
    and an activity line each time the model is down."""
    from arail.registry import get_registry
    return get_registry().peek("fast", tab="agents")


def probe_structured_output(entry: Any) -> bool:
    """True if the Ollama behind ``entry`` takes a JSON-schema ``format`` (>= 0.5). Cached. (seam)"""
    root = ollama_root(entry)
    now = time.monotonic()
    with _probe_lock:
        hit = _probe_cache.get(root)
        if hit is not None and hit[0] > now:
            return hit[1]
    ok = False
    try:
        import requests
        resp = requests.get(root + "/api/version", timeout=1.5)
        resp.raise_for_status()
        parts = str(resp.json().get("version", "")).split("-")[0].split(".")
        ok = (int(parts[0]), int(parts[1])) >= MIN_OLLAMA
    except Exception:  # noqa: BLE001 - an unreachable or odd Ollama reads as "not supported"
        ok = False
    with _probe_lock:
        _probe_cache[root] = (now + (_PROBE_OK_TTL_S if ok else _PROBE_FAIL_TTL_S), ok)
    return ok


def catalog_family(model_id: str) -> Optional[str]:
    """The model catalog's ``family`` for ``model_id`` (tag stripped), or None. (seam)"""
    try:
        from arail.chat import load_catalog
        bare = (model_id or "").split(":", 1)[0]
        for e in load_catalog():
            if e.id == bare:
                return e.family
    except Exception:  # noqa: BLE001
        pass
    return None


# --------------------------------------------------------------------------- decisions
def ollama_root(entry: Any) -> str:
    """The Ollama root URL (the endpoint without a trailing ``/v1``); loopback default when unset."""
    import os
    base = (getattr(entry, "endpoint", None) or f"http://127.0.0.1:{os.getenv('OLLAMA_PORT', '11434')}/v1").rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def entry_is_local(entry: Any) -> bool:
    """Local means: not a cloud entry, an ``ollama_native`` backend, not a remote runtime, loopback host."""
    from arail.registry.core import is_cloud_entry
    if is_cloud_entry(entry):
        return False
    if getattr(entry, "provider_type", None) in NON_LOCAL_PROVIDERS:
        return False
    if getattr(entry, "backend", None) != "ollama_native":
        return False
    host = urlparse(ollama_root(entry)).hostname or ""
    return host in LOOPBACK_HOSTS


def describe() -> BrainView:
    """What the handshake reports and brain.chat enforces. Never raises."""
    try:
        entry = resolve_entry()
    except Exception:  # noqa: BLE001 - a registry hiccup is "no brain", fail closed
        entry = None
    if entry is None:
        return BrainView(False, False, None, None, False, None, "brain_unavailable")
    try:
        local = entry_is_local(entry)
    except Exception:  # noqa: BLE001
        local = False
    if not local:
        return BrainView(False, False, None, None, False, None, "brain_not_local")
    try:
        structured = bool(probe_structured_output(entry))
    except Exception:  # noqa: BLE001
        structured = False
    try:
        family = catalog_family(entry.model_id)
    except Exception:  # noqa: BLE001
        family = None
    return BrainView(True, True, str(entry.model_id)[:128], "ollama_native", structured,
                     "Built with Llama" if family == "llama" else None, None, entry)


def build_router() -> Any:
    """A ModelRouter for the ``fast`` profile, or an EdgeError. (seam)

    Uses the real ``resolve`` (not ``peek``): a phone question that hits a dead model
    should leave the registry's visible fallback event, like any other caller.
    """
    from arail.registry import get_registry
    res = get_registry().resolve("fast", tab="agents")
    if res.entry is None:
        raise EdgeError("brain_unavailable")
    if not entry_is_local(res.entry):
        raise EdgeError("brain_not_local")
    router = res.router(billing_source="agent")
    if router is None:
        raise EdgeError("brain_unavailable")
    if getattr(router, "backend_name", None) != "ollama_native":
        raise EdgeError("brain_not_local")
    return router


# --------------------------------------------------------------------------- request handling
@dataclass(frozen=True)
class TurnHeader:
    trace_id: str
    channel: str
    pseudonym: str
    role: str
    response_schema: Optional[dict]


def message_text(content: Any) -> str:
    """A message's text: a string as is, a list of text parts joined with LF."""
    if isinstance(content, str):
        return content
    return "\n".join(str(part.get("text", "")) for part in content)


def check_tools_and_content(body: dict) -> None:
    """Step 3: no tools reach the model, and nothing but text parts."""
    if body.get("tools"):
        raise EdgeError("tools_not_allowed")
    for msg in body["messages"]:
        content = msg["content"]
        if isinstance(content, str):
            continue
        for part in content:
            if (not isinstance(part, dict) or set(part) != {"type", "text"} or part["type"] != "text"
                    or not isinstance(part["text"], str) or len(part["text"]) > int(_LIM["message_chars_max"])):
                raise EdgeError("content_not_allowed")


def check_shape(body: dict) -> None:
    """Rules JSON Schema cannot express: the last message is the user's."""
    if body["messages"][-1]["role"] != "user":
        raise EdgeError("schema_invalid")


def check_size(body: dict) -> None:
    """Step 4."""
    if sum(len(message_text(m["content"])) for m in body["messages"]) > TOTAL_CHARS_MAX:
        raise EdgeError("prompt_too_large")


def _has_remote_ref(node: Any) -> bool:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("$ref", "$dynamicRef", "$recursiveRef") and not (isinstance(v, str) and v.startswith("#")):
                return True
            if _has_remote_ref(v):
                return True
    elif isinstance(node, list):
        return any(_has_remote_ref(v) for v in node)
    return False


def parse_header(first_content: Any) -> TurnHeader:
    """Step 5: line 1 of ``messages[0]`` is ``BUDDY-TURN/1 <one-line JSON>``. The edge reads only that line."""
    from jsonschema import Draft202012Validator
    text = message_text(first_content)
    line = text.split("\n", 1)[0]
    if not line.startswith(HEADER_PREFIX):
        raise EdgeError("turn_header_invalid")
    try:
        data = json.loads(line[len(HEADER_PREFIX):])
    except ValueError:
        raise EdgeError("turn_header_invalid") from None
    if next(Draft202012Validator(CONTRACT["turn_header"]["schema"]).iter_errors(data), None) is not None:
        raise EdgeError("turn_header_invalid")
    schema = data["response_schema"]
    if schema is not None:
        encoded = json.dumps(schema, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > RESPONSE_SCHEMA_BYTES_MAX or _has_remote_ref(schema):
            raise EdgeError("turn_header_invalid")
    who = data["identity"]
    return TurnHeader(data["trace_id"], data["channel"], who["pseudonym"], who["role"], schema)


def flatten(messages: list) -> tuple[str, str]:
    """``(system, prompt)``. ARAIL's Ollama backend sends one user message, so the conversation is
    flattened deterministically: ``system`` is ``messages[0]`` without its header line; ``prompt`` is
    ``### <role>`` LF content LF LF for every later message."""
    first = message_text(messages[0]["content"])
    system = first.split("\n", 1)[1] if "\n" in first else ""
    prompt = "".join(f"### {m['role']}\n{message_text(m['content'])}\n\n" for m in messages[1:])
    return system, prompt


def max_tokens_of(body: dict) -> int:
    asked = [v for v in (body.get("max_tokens"), body.get("max_completion_tokens")) if v is not None]
    return min([MAX_TOKENS_MAX, *asked])


def estimate_prompt_tokens(system: str, prompt: str) -> int:
    """The same estimate the router chokepoint uses for ``tokens_in``."""
    return max((len(prompt) + len(system)) // 4, 1)


def _reset_for_tests() -> None:
    with _probe_lock:
        _probe_cache.clear()
