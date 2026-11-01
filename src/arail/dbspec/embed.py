"""Embedding provider for the ARAIL 2.0 vector stores.

One model, globally, declared in ``spec/models/models.hcl`` and served through
Ollama — already a hard dependency, so this adds no new runtime dependency and
works airgapped once the model is pulled.

**This module never falls back to a different embedding.** That is the whole
design, and it is worth stating plainly because a fallback looks helpful and
is not: vectors from two different models occupy unrelated spaces, so mixing
them into one index does not degrade recall gracefully — it makes the distance
metric meaningless while every query still returns confident-looking results.
1.x shipped a 128-dim SHA1 token-hash projection under the name "semantic
search"; the lesson is that silent substitution is indistinguishable from
working software right up until someone checks.

So: if the embedding model is unavailable, ingest fails loudly and says how to
fix it. The rows that already exist keep their recorded provenance in
``content_refs``, and ``./arailctl db doctor`` reports any that disagree with
the current spec.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable, List, Optional, Sequence

from arail.dbspec.generated.models_registry import EMBEDDING_DIM, embedding_model

__all__ = [
    "EmbeddingError", "EmbeddingUnavailable", "DimensionMismatch",
    "embed_texts", "embed", "embed_documents", "embed_query",
    "ollama_root", "probe",
]

_TIMEOUT_SEC = float(os.getenv("ARAIL_EMBED_TIMEOUT_SEC", "60"))
_BATCH = int(os.getenv("ARAIL_EMBED_BATCH", "32"))


class EmbeddingError(RuntimeError):
    """Base class. Every message says what to do next."""


class EmbeddingUnavailable(EmbeddingError):
    """The embedding model could not be reached or is not installed."""


class DimensionMismatch(EmbeddingError):
    """The served model returned a dimension the spec does not declare."""


def ollama_root() -> str:
    """Base URL for the local Ollama server.

    Mirrors the convention in ``arail.router.backends``: an explicit
    ``MODEL_API_BASE`` wins, otherwise ``OLLAMA_PORT`` on loopback.
    """
    explicit = os.getenv("MODEL_API_BASE", "").strip()
    if explicit:
        return explicit.rstrip("/").removesuffix("/v1")
    host = os.getenv("OLLAMA_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if "://" in host:
        return host.rstrip("/")
    port = os.getenv("OLLAMA_PORT", "11434").strip() or "11434"
    return f"http://{host}:{port}"


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_LOGGED_HYBRID_EGRESS = False


def _assert_local(base: str) -> None:
    """Refuse a non-loopback embedding provider unless ``LAB_MODE=hybrid``.

    ``MODEL_API_BASE`` is operator-settable and, unguarded, is a corpus-text
    egress path: every PKB row's text is sent to whatever host this resolves
    to. In the default ``airgapped`` mode that must be impossible. In
    ``hybrid`` it is allowed (the operator opted in to cloud providers
    elsewhere) and logged once at INFO so it isn't a silent surprise.
    """
    global _LOGGED_HYBRID_EGRESS
    host = urllib.parse.urlparse(base).hostname or ""
    if host in _LOCAL_HOSTS:
        return
    lab_mode = os.getenv("LAB_MODE", "airgapped").strip().lower()
    if lab_mode == "hybrid":
        if not _LOGGED_HYBRID_EGRESS:
            import logging
            logging.getLogger(__name__).info(
                "embedding provider %r is non-loopback; allowed because "
                "LAB_MODE=hybrid", base)
            _LOGGED_HYBRID_EGRESS = True
        return
    raise EmbeddingError(
        f"MODEL_API_BASE={base!r} is not a loopback address and "
        f"LAB_MODE={lab_mode!r} is not 'hybrid'. Corpus text would be sent "
        f"off this machine. Either unset MODEL_API_BASE (or point it at "
        f"127.0.0.1/localhost), or set LAB_MODE=hybrid to opt in.")


def _post(path: str, payload: dict, *, timeout: float) -> dict:
    base = ollama_root()
    _assert_local(base)
    url = f"{base}{path}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        model = embedding_model()
        if exc.code == 404:
            raise EmbeddingUnavailable(
                f"the embedding model {model.name!r} is not installed in "
                f"Ollama at {ollama_root()}.\n"
                f"Install it with:  ollama pull {model.ollama_tag or model.name}\n"
                f"(server said: {detail.strip()})"
            ) from exc
        raise EmbeddingError(
            f"embedding request to {url} failed with HTTP {exc.code}: "
            f"{detail.strip()}") from exc
    except urllib.error.URLError as exc:
        raise EmbeddingUnavailable(
            f"cannot reach Ollama at {ollama_root()} ({exc.reason}).\n"
            f"Start it with:  ollama serve\n"
            f"Then install the embedding model:  "
            f"ollama pull {embedding_model().ollama_tag or embedding_model().name}"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise EmbeddingUnavailable(
            f"embedding request to {url} timed out or failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EmbeddingError(
            f"embedding response from {url} was not valid JSON: {exc}") from exc


def probe() -> tuple[bool, str]:
    """Check whether embedding is usable right now.

    Returns ``(ok, message)``. Callers that need to report readiness without
    raising — the CLI's ``doctor``, setup — use this.
    """
    model = embedding_model()
    try:
        vector = embed("probe")
    except EmbeddingError as exc:
        return False, str(exc)
    return True, (f"{model.name} reachable at {ollama_root()}, "
                  f"{len(vector)} dimensions")


def embed_texts(texts: Sequence[str], *, prefix: str = "") -> List[List[float]]:
    """Embed a batch. Raises rather than returning a substitute vector.

    The returned vectors are in the same order as ``texts``. Prefer
    :func:`embed_documents` and :func:`embed_query`, which apply the task
    prefixes the model was trained with.
    """
    if not texts:
        return []
    model = embedding_model()
    tag = model.ollama_tag or model.name

    vectors: List[List[float]] = []
    for start in range(0, len(texts), _BATCH):
        chunk = [prefix + (t if isinstance(t, str) else str(t))
                 for t in texts[start:start + _BATCH]]
        payload = {"model": tag, "input": chunk}
        data = _post("/api/embed", payload, timeout=_TIMEOUT_SEC)
        batch = data.get("embeddings")
        if not isinstance(batch, list) or len(batch) != len(chunk):
            raise EmbeddingError(
                f"Ollama returned {len(batch) if isinstance(batch, list) else 0} "
                f"embeddings for {len(chunk)} inputs at {ollama_root()}; "
                f"expected one per input")
        for vector in batch:
            if not isinstance(vector, list) or len(vector) != EMBEDDING_DIM:
                got = len(vector) if isinstance(vector, list) else "non-list"
                raise DimensionMismatch(
                    f"model {tag!r} returned {got} dimensions but the spec "
                    f"declares {EMBEDDING_DIM}. Schema versioning is global: "
                    f"every world shares one dimension. Either the installed "
                    f"model is not {model.base!r}, or spec/models/models.hcl "
                    f"is wrong. Run './arailctl db doctor' for the per-world "
                    f"breakdown.")
            vectors.append([float(v) for v in vector])
    return vectors


def embed_documents(texts: Sequence[str]) -> List[List[float]]:
    """Embed content for storage, with the model's document prefix.

    nomic-embed-text is trained asymmetrically: documents and queries get
    different prefixes, and omitting them measurably narrows the margin
    between a relevant and an irrelevant hit. The prefixes are declared in
    spec/models/models.hcl rather than hardcoded here, because which prefix a
    model wants is a property of the model.
    """
    return embed_texts(texts, prefix=embedding_model().document_prefix)


def embed_query(text: str) -> List[float]:
    """Embed a search query, with the model's query prefix."""
    return embed_texts([text], prefix=embedding_model().query_prefix)[0]


def embed(text: str) -> List[float]:
    """Embed a single string with no task prefix.

    Use :func:`embed_documents` / :func:`embed_query` for retrieval; this is
    for probes and for callers that supply their own prefix.
    """
    return embed_texts([text])[0]
