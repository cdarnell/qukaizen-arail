"""ARAIL model registry — the only model resolution path.

GENERATED FILE — DO NOT EDIT.

Produced by ``arail.dbspec.codegen`` from the spec tree. Hand edits are lost
on the next ``./arailctl db apply``. Change the spec, not this file.

    spec sha256: 212d8c12bbf196a263050fd2c33529846aee67bccd5fe7e80757a3fdfc64a3aa
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

__all__ = [
    "Model", "MODELS", "EMBEDDING_DIM", "EMBEDDING_MODEL",
    "ANSWERING_CEILING", "ModelNotFound", "get_model", "models_for_role",
    "default_model", "answering_model", "embedding_model",
]


class ModelNotFound(LookupError):
    """Asked for a model the spec does not declare."""


@dataclass(frozen=True)
class Model:
    name: str
    role: str
    backend: str
    parameter_count: int
    parameter_source: str
    base: Optional[str]
    ollama_tag: Optional[str]
    license: Optional[str]
    disclosure: Optional[str]
    tier: str
    default: bool
    embedding_dim: Optional[int]
    query_prefix: str
    document_prefix: str

    @property
    def eligible(self) -> bool:
        return self.parameter_count >= 0


@dataclass(frozen=True)
class Ceiling:
    name: str
    role: str
    max_parameters: int
    bound: str
    rationale: str

    def violated_by(self, parameter_count: int) -> bool:
        if self.bound == "exclusive":
            return parameter_count >= self.max_parameters
        return parameter_count > self.max_parameters

ANSWERING_CEILING = Ceiling(
    name='answering',
    role='answering',
    max_parameters=8000000000,
    bound='exclusive',
    rationale='8B+ answering models do not fit the local-first promise on a 16GB machine, and a cloud escape hatch would silently break airgapped mode.',
)

MODELS: Dict[str, Model] = {
    'ai-engineer': Model(
        name='ai-engineer',
        role='answering',
        backend='ollama',
        parameter_count=7615616512,
        parameter_source='hf_config',
        base='Qwen/Qwen2.5-7B-Instruct',
        ollama_tag='ai-engineer',
        license='apache-2.0',
        disclosure=None,
        tier='maximus',
        default=False,
        embedding_dim=None,
        query_prefix='',
        document_prefix='',
    ),
    'llama-ai-eng': Model(
        name='llama-ai-eng',
        role='answering',
        backend='ollama',
        parameter_count=1235814432,
        parameter_source='hf_config',
        base='meta-llama/Llama-3.2-1B-Instruct',
        ollama_tag='llama-ai-eng',
        license='llama-3.2-community',
        disclosure='Built with Llama',
        tier='minimalist',
        default=True,
        embedding_dim=None,
        query_prefix='',
        document_prefix='',
    ),
    'nomic-embed-text': Model(
        name='nomic-embed-text',
        role='embedding',
        backend='ollama',
        parameter_count=136731648,
        parameter_source='hf_config',
        base='nomic-ai/nomic-embed-text-v1.5',
        ollama_tag='nomic-embed-text',
        license='apache-2.0',
        disclosure=None,
        tier='minimalist',
        default=True,
        embedding_dim=768,
        query_prefix='search_query: ',
        document_prefix='search_document: ',
    ),
}

EMBEDDING_MODEL = 'nomic-embed-text'
EMBEDDING_DIM = 768


def get_model(name: str) -> Model:
    """Resolve a model by name. Raises rather than guessing."""
    try:
        return MODELS[name]
    except KeyError:
        raise ModelNotFound(
            f"no model named {name!r} in the registry. Declared models: "
            f"{', '.join(sorted(MODELS))}. Models are declared in "
            f"spec/models/models.hcl and compiled by './arailctl db apply'."
        ) from None


def models_for_role(role: str) -> Tuple[Model, ...]:
    """Eligible models for a role, in declaration-independent (sorted) order.

    A model whose parameter count is undeclared is never eligible: filenames
    are not evidence of size, and the ceiling cannot be checked against a
    guess.
    """
    return tuple(sorted(
        (m for m in MODELS.values() if m.role == role and m.eligible),
        key=lambda m: m.name,
    ))


def default_model(role: str) -> Model:
    """The declared default for a role."""
    candidates = [m for m in models_for_role(role) if m.default]
    if not candidates:
        raise ModelNotFound(
            f"no default model declared for role {role!r}. Set default = true "
            f"on exactly one {role!r} model in spec/models/models.hcl."
        )
    if len(candidates) > 1:
        raise ModelNotFound(
            f"role {role!r} has {len(candidates)} defaults: "
            f"{', '.join(m.name for m in candidates)}. Exactly one is required."
        )
    return candidates[0]


def answering_model() -> Model:
    """The model that serves answers.

    The ceiling is enforced at compile time — a spec declaring an answering
    model at or above the ceiling does not build — so this re-check exists
    only to catch a hand-edited generated file. There is no override.
    """
    model = default_model("answering")
    if ANSWERING_CEILING.violated_by(model.parameter_count):
        raise ModelNotFound(
            f"{model.name!r} has {model.parameter_count:,} parameters and "
            f"violates the answering ceiling of "
            f"{ANSWERING_CEILING.max_parameters:,}. This file was hand-edited; "
            f"regenerate it with './arailctl db apply'."
        )
    return model


def embedding_model() -> Model:
    """The single global embedding model.

    Schema versioning is global: all worlds share one embedding model and one
    vector dimension at any spec version, which is what makes per-world drift
    detectable as corruption rather than configuration.
    """
    return get_model(EMBEDDING_MODEL)
