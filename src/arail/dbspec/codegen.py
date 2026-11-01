"""Compile the spec tree into generated Python.

Two artifacts, both committed and both marked generated:

``generated/models_registry.py``
    The only model resolution path. Every call site that needs a model asks
    this module; nothing resolves a model any other way.

``generated/world_resolver.py``
    Explicit id or slug only. There is no fallback branch to generate — the
    spec forbids positional, first-available, most-recent, and alphabetical
    resolution, and :mod:`arail.dbspec.spec` refuses to load a spec that asks
    for one. That is why this generator has no code path that emits a
    fallback: the absence is structural, not a policy comment.

Generated code depends on nothing but the standard library, so a lab that has
not installed the build tooling can still import and use it.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import List

from arail.dbspec.spec import Spec, load_spec

__all__ = ["generate_all", "render_models_registry", "render_world_resolver",
           "GENERATED_DIR"]

GENERATED_DIR = Path("src/arail/dbspec/generated")

_BANNER = '''"""{title}

GENERATED FILE — DO NOT EDIT.

Produced by ``arail.dbspec.codegen`` from the spec tree. Hand edits are lost
on the next ``./arailctl db apply``. Change the spec, not this file.

    spec sha256: {sha}
"""
'''


def _q(value: object) -> str:
    """Repr that is stable across runs (no dict ordering surprises)."""
    return repr(value)


# ---------------------------------------------------------------------------
# models_registry.py
# ---------------------------------------------------------------------------

def render_models_registry(spec: Spec) -> str:
    out: List[str] = []
    out.append(_BANNER.format(
        title="ARAIL model registry — the only model resolution path.",
        sha=spec.sha256))
    out.append("""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

__all__ = [
    "Model", "MODELS", "EMBEDDING_DIM", "EMBEDDING_MODEL",
    "ANSWERING_CEILING", "ModelNotFound", "get_model", "models_for_role",
    "default_model", "answering_model", "embedding_model",
]


class ModelNotFound(LookupError):
    \"\"\"Asked for a model the spec does not declare.\"\"\"


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

""")

    # Ceilings
    ceiling = next((c for c in spec.ceilings if c.role == "answering"), None)
    if ceiling is None:  # pragma: no cover - validated upstream
        raise ValueError("spec declares no answering ceiling")
    out.append(
        "ANSWERING_CEILING = Ceiling(\n"
        f"    name={_q(ceiling.name)},\n"
        f"    role={_q(ceiling.role)},\n"
        f"    max_parameters={ceiling.max_parameters},\n"
        f"    bound={_q(ceiling.bound)},\n"
        f"    rationale={_q(ceiling.rationale)},\n"
        ")\n\n"
    )

    out.append("MODELS: Dict[str, Model] = {\n")
    for m in sorted(spec.models, key=lambda x: x.name):
        out.append(
            f"    {_q(m.name)}: Model(\n"
            f"        name={_q(m.name)},\n"
            f"        role={_q(m.role)},\n"
            f"        backend={_q(m.backend)},\n"
            f"        parameter_count={m.parameter_count},\n"
            f"        parameter_source={_q(m.parameter_source)},\n"
            f"        base={_q(m.base)},\n"
            f"        ollama_tag={_q(m.ollama_tag)},\n"
            f"        license={_q(m.license)},\n"
            f"        disclosure={_q(m.disclosure)},\n"
            f"        tier={_q(m.tier)},\n"
            f"        default={m.default},\n"
            f"        embedding_dim={_q(m.embedding_dim)},\n"
            f"        query_prefix={_q(m.query_prefix)},\n"
            f"        document_prefix={_q(m.document_prefix)},\n"
            f"    ),\n"
        )
    out.append("}\n\n")

    emb = spec.embedding_model
    out.append(f"EMBEDDING_MODEL = {_q(emb.name)}\n")
    out.append(f"EMBEDDING_DIM = {spec.embedding_dim}\n\n")

    out.append('''
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
''')
    return "".join(out)


# ---------------------------------------------------------------------------
# world_resolver.py
# ---------------------------------------------------------------------------

def render_world_resolver(spec: Spec) -> str:
    r = spec.resolver
    resolvable = sorted(s.name for s in spec.statuses if s.resolvable)
    selectable = sorted(s.name for s in spec.statuses if s.selectable)
    selectable_placeholders = ", ".join("?" for _ in selectable)

    out: List[str] = []
    out.append(_BANNER.format(
        title="ARAIL world resolver — explicit id or slug only.",
        sha=spec.sha256))
    out.append(f'''
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple

__all__ = [
    "World", "WorldNotFound", "AmbiguousWorldRequest", "resolve_world",
    "list_worlds", "RESOLVABLE_STATUSES", "SELECTABLE_STATUSES",
]

# Statuses a world may have and still be resolvable by explicit identifier.
# Resolving an archived world is not an error; silently substituting a
# different world would be.
RESOLVABLE_STATUSES: Tuple[str, ...] = {tuple(resolvable)!r}

# Statuses a world may have and still be offered as a choice in a picker.
SELECTABLE_STATUSES: Tuple[str, ...] = {tuple(selectable)!r}


class WorldNotFound(LookupError):
    """No world matched the requested identifier.

    Carries the requested identifier, the reason, and the valid alternatives
    for that user — the caller should never have to guess, and must never
    fall back to a different world.
    """

    def __init__(self, requested: str, reason: str,
                 alternatives: Tuple[str, ...], user_id: str) -> None:
        self.requested = requested
        self.reason = reason
        self.alternatives = alternatives
        self.user_id = user_id
        if alternatives:
            available = "available for this user: " + ", ".join(alternatives)
        else:
            available = "this user has no worlds"
        super().__init__(
            f"world {{requested!r}} could not be resolved ({{reason}}); "
            f"{{available}}"
        )


class AmbiguousWorldRequest(ValueError):
    """Both an id and a slug were supplied, or neither."""


@dataclass(frozen=True)
class World:
    id: str
    slug: str
    user_id: str
    display_name: str
    status: str
    bundle_dir: Optional[str]
    created_at: str
    updated_at: str


def _row_to_world(row: sqlite3.Row) -> World:
    return World(
        id=row["id"], slug=row["slug"], user_id=row["user_id"],
        display_name=row["display_name"], status=row["status"],
        bundle_dir=row["bundle_dir"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _alternatives(conn: sqlite3.Connection, user_id: str) -> Tuple[str, ...]:
    rows = conn.execute(
        "SELECT slug FROM worlds WHERE user_id = ? AND status IN ({selectable_placeholders}) "
        "ORDER BY slug",
        (user_id, *SELECTABLE_STATUSES),
    ).fetchall()
    return tuple(row["slug"] for row in rows)


def list_worlds(conn: sqlite3.Connection, *, user_id: str,
                selectable_only: bool = False) -> Tuple[World, ...]:
    """Worlds belonging to ``user_id``.

    Ordered by slug for stable display. Callers must NOT treat this order as
    meaningful: there is no "first" world, and indexing into this tuple to
    pick one is the positional-resolution defect this module exists to
    remove.
    """
    statuses = SELECTABLE_STATUSES if selectable_only else RESOLVABLE_STATUSES
    placeholders = ", ".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT * FROM worlds WHERE user_id = ? AND status IN ({{placeholders}}) "
        f"ORDER BY slug",
        (user_id, *statuses),
    ).fetchall()
    return tuple(_row_to_world(row) for row in rows)


def resolve_world(conn: sqlite3.Connection, *, user_id: str,
                  world_id: Optional[str] = None,
                  slug: Optional[str] = None) -> World:
    """Resolve exactly one world by explicit id or slug.

    There is no fallback. If the identifier does not match, this raises
    :class:`WorldNotFound` naming the request and the alternatives; it never
    returns a different world, the only world, the newest world, or the
    alphabetically first world.
    """
    if (world_id is None) == (slug is None):
        raise AmbiguousWorldRequest(
            "supply exactly one of world_id= or slug= "
            f"(got world_id={{world_id!r}}, slug={{slug!r}})"
        )

    if world_id is not None:
        requested, column, value = world_id, "id", world_id
    else:
        requested, column, value = slug, "slug", slug

    row = conn.execute(
        f"SELECT * FROM worlds WHERE user_id = ? AND {{column}} = ?",
        (user_id, value),
    ).fetchone()

    if row is None:
        raise WorldNotFound(
            requested=str(requested),
            reason=f"no world with that {{column}} belongs to user {{user_id!r}}",
            alternatives=_alternatives(conn, user_id),
            user_id=user_id,
        )

    world = _row_to_world(row)
    if world.status not in RESOLVABLE_STATUSES:
        raise WorldNotFound(
            requested=str(requested),
            reason=f"world has status {{world.status!r}}, which is not resolvable",
            alternatives=_alternatives(conn, user_id),
            user_id=user_id,
        )
    return world
''')
    return "".join(out)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def generate_all(spec: Spec | None = None, *,
                 out_dir: str | Path = GENERATED_DIR) -> list[Path]:
    """Render every generated artifact. Returns the paths written."""
    spec = spec or load_spec()
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    init = target / "__init__.py"
    if not init.exists():
        init.write_text(
            '"""Generated from the ARAIL spec tree. Do not hand-edit."""\n',
            encoding="utf-8")

    written: list[Path] = []
    for name, render in (("models_registry.py", render_models_registry),
                         ("world_resolver.py", render_world_resolver)):
        path = target / name
        path.write_text(render(spec), encoding="utf-8")
        written.append(path)
    written.append(init)
    return written


if __name__ == "__main__":  # pragma: no cover
    for path in generate_all():
        print(f"generated {path.resolve()}")
