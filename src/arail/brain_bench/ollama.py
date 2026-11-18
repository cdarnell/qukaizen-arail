"""Loopback-only Ollama reads and residency control for the harness.

Never calls ``/api/pull`` or ``/api/create``; ``/api/generate`` is used only
with no prompt, to load/unload/pin a model that is already installed."""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from arail import model_pins

_TIMEOUT_S = 10.0
FAR = timedelta(hours=24)


class OllamaDown(RuntimeError):
    pass


def base() -> Optional[str]:
    from arail import model_resolve
    return model_resolve._ollama_base()


class Ollama:
    def __init__(self, root: Optional[str] = None):
        self.root = root or base()
        if self.root is None:
            raise OllamaDown("not_loopback")

    def _req(self, path: str, body: Optional[dict] = None, timeout: float = _TIMEOUT_S) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.root + path, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except Exception as exc:  # noqa: BLE001 - fixed text only, never a value from the machine
            raise OllamaDown(type(exc).__name__) from None

    def version(self) -> str:
        return str(self._req("/api/version").get("version", ""))

    def tags(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for m in self._req("/api/tags").get("models") or []:
            name = m.get("name") or m.get("model")
            if name:
                out[str(name)] = m
        return out

    def show(self, tag: str) -> Dict[str, Any]:
        return self._req("/api/show", {"model": tag})

    def ps(self) -> List[Dict[str, Any]]:
        return list(self._req("/api/ps").get("models") or [])

    def identity(self, tag: str) -> Dict[str, Any]:
        """Manifest digest, weights sha256, runner, parameter size, quantization, context and capabilities for *tag*."""
        meta = self.tags().get(tag)
        if meta is None:
            raise OllamaDown("not_installed")
        show = self.show(tag)
        det = meta.get("details") or {}
        info = show.get("model_info") or {}
        return {"tag": tag, "manifest_digest": meta.get("digest"),
                "weights_sha256": model_pins.weights_from_show(show),
                "runner": det.get("runner") or show.get("details", {}).get("runner"),
                "family": det.get("family"), "parameter_size": det.get("parameter_size"),
                "quantization": det.get("quantization_level"),
                "capabilities": list(show.get("capabilities") or []),
                "arch_context": next((v for k, v in info.items() if k.endswith(".context_length")), None)}

    def keep_alive(self, tag: str, value: Any) -> None:
        self._req("/api/generate", {"model": tag, "keep_alive": value}, timeout=120)


def _parse_ts(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    t = re.sub(r"(\.\d{6})\d+", r"\1", text.replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def residency(ps: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """name -> {pinned, size_vram, context_length}; pinned = expires more than 24 h from now."""
    now = datetime.now(timezone.utc)
    out = {}
    for m in ps:
        exp = _parse_ts(m.get("expires_at"))
        out[str(m.get("name") or m.get("model"))] = {
            "pinned": bool(exp and exp - now > FAR), "size_vram": m.get("size_vram"),
            "context_length": m.get("context_length")}
    return out


def restore(o: Ollama, before: Dict[str, Dict[str, Any]], touched: List[str]) -> Dict[str, Any]:
    """Put residency back: unload what the run loaded, re-pin what was pinned before."""
    import time
    for tag in touched:
        was = before.get(tag)
        try:
            if was is None:
                o.keep_alive(tag, 0)
            elif was["pinned"]:
                o.keep_alive(tag, -1)
        except OllamaDown:
            pass
    after: Dict[str, Dict[str, Any]] = {}
    for _ in range(10):
        after = residency(o.ps())
        if set(after) <= set(before) and all(after.get(n, {}).get("pinned") == v["pinned"] and n in after
                                              for n, v in before.items() if v["pinned"]):
            break
        time.sleep(0.5)
    restored = (not (set(after) - set(before))) and all(
        n in after and after[n]["pinned"] for n, v in before.items() if v["pinned"])
    return {"restored": restored, "ps_after": sorted(after)}
