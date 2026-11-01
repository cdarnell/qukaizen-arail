"""world_mount — ARAIL-side host for a DaC WorldBundle.

Reads a WorldBundle directory (7 portable JSON files) and provides:
- Integrity verification (dual sha256 check)
- Compatibility + category gating
- Atomic mount/unmount/swap with pointer at lab/data/world-mount.json
- KB staging into lab/pkb/sources/world-<slug>/
- Consumer helpers for dictionary, face, and curator integration
- __main__ CLI: list | mount | verify | swap | unmount

Security boundary
-----------------
terms.json is DATA; it never enters a prompt. Only face.json text may
parameterize prompts, and only through the delimited, length-capped Buddy
WORLD FRAMING block. Terms reach users only through template-rendered surfaces
(dictionary page) that never round-trip a model.

Consent model: mounting a World IS the operator's consent to adopt that World's
identity. Identity (name, logo, theme, intent, framing) resolves live from the
mount sidecar (``world-mount.json``) at request time via ``arail.identity`` —
there is no ``--apply-face`` step and no ``.env`` write. The sidecar is the
single, cross-restart source of truth; identity flips instantly on mount and
reverts on unmount, no restart required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MOUNT_RECORD_NAME = "world-mount.json"
CAPABILITIES_SIDECAR_NAME = "world-capabilities.json"
MODEL_SIDECAR_NAME = "world-model.json"
_STAGING_DIR_SUFFIX = ".staging"

# model.json (seal-EXEMPT, OPTIONAL sibling) — schema string + field guards.
_MODEL_HINT_SCHEMA = "dac.world-model/v1"
# Conservative allowlist for a recommended.id (ollama model:tag OR catalog id).
# Defense-in-depth: the id is only ever compared against catalog ids and
# rendered as escaped text — NEVER shell-interpolated by ARAIL.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,128}$")
_MODEL_RATIONALE_CAP = 280

# Seal-EXEMPT sibling staged at mount time (NOT in _BUNDLE_FILES — adding it
# there would make verify_seal demand a hash that doesn't exist for every
# existing 6-file bundle). Loaded by skills_loader.load_world_skill() as
# untrusted DATA with load-time containment.
_WORLD_SKILL_NAME = "SKILL.md"

# Files listed in manifest.files{} (not manifest.json itself)
_BUNDLE_FILES = frozenset([
    "agenda.json",
    "drift-report.json",
    "face.json",
    "roster.json",
    "spec.json",
    "terms.json",
])

# Staged machinery files: they stay ON DISK (the mount contract reads them via
# mounted_terms/world_trusted_domains/etc.) but are excluded from every KB
# surface — pkb.browse, pkb.search/_iter_pkb_files, and pkb_index rows — via
# the ONE shared predicate is_world_machinery_path(). terms.json is machinery
# too: its content reaches the KB as per-term pages under terms/ instead of
# one hashed JSON blob. SKILL.md, world-<slug>.md, face.json and terms/*.md
# stay indexed.
WORLD_MACHINERY_FILES = frozenset([
    "agenda.json",
    "drift-report.json",
    "roster.json",
    "spec.json",
    "terms.json",
])


def is_world_machinery_path(path: "Path | str") -> bool:
    """True when *path* is a staged World machinery file directly inside a
    ``sources/world-*/`` dir. Shared by pkb.browse, pkb._iter_pkb_files and
    pkb_index — do not duplicate the filename list at call sites. Never raises.
    """
    try:
        p = Path(path)
        if p.name not in WORLD_MACHINERY_FILES:
            return False
        parent = p.parent
        return parent.name.startswith("world-") and parent.parent.name == "sources"
    except Exception:  # noqa: BLE001
        return False

# ── Error hierarchy ──────────────────────────────────────────────────────────


class BundleError(Exception):
    """Base for all WorldBundle errors. .user_message is operator-actionable."""

    def __init__(self, message: str, *, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or message


class PartialBundle(BundleError):
    """One or more required files are missing or unreadable."""


class SealMismatch(BundleError):
    """sha256 of terms.json does not match the manifest."""


class SchemaSkew(BundleError):
    """bundle_schema or terms_schema version is unsupported."""


class GateViolation(BundleError):
    """A term uses a category not declared in spec.categories."""


class FaceInvalid(BundleError):
    """face.json is missing or structurally invalid."""


class SlugInvalid(BundleError):
    """The world slug fails the allowlist or is inconsistent across bundle files."""


# Slug allowlist: lowercase letters, digits, and hyphens; must start with a letter or digit.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ── Data types ───────────────────────────────────────────────────────────────


@dataclass
class Bundle:
    """Parsed, in-memory representation of a loaded WorldBundle."""

    bundle_dir: Path
    manifest: Dict[str, Any]
    terms_raw_bytes: bytes          # raw bytes of terms.json (for sha256)
    terms_data: Dict[str, Any]      # parsed terms.json (version + terms list)
    spec: Dict[str, Any]
    face: Optional[Dict[str, Any]]  # None if missing/invalid
    roster: Dict[str, Any]
    drift_report: Dict[str, Any]
    agenda: Dict[str, Any]

    @property
    def slug(self) -> str:
        return str(self.manifest.get("world", "world"))

    @property
    def world(self) -> str:
        return str(self.manifest.get("world", "world"))

    @property
    def bundle_version(self) -> int:
        return int(self.manifest.get("bundle_version", 1))

    @property
    def terms(self) -> List[Dict[str, Any]]:
        return list(self.terms_data.get("terms", []))

    @property
    def categories(self) -> List[Dict[str, Any]]:
        return list(self.spec.get("categories", []))


@dataclass
class SealResult:
    ok: bool
    computed_sha256: str
    manifest_world_sha256: str
    manifest_files_sha256: str
    user_message: str


@dataclass
class MountRecord:
    world: str
    bundle_version: int
    world_sha256: str
    mounted_at: str
    bundle_dir: str
    staged_dir: str
    pin: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "world": self.world,
            "bundle_version": self.bundle_version,
            "world_sha256": self.world_sha256,
            "mounted_at": self.mounted_at,
            "bundle_dir": self.bundle_dir,
            "staged_dir": self.staged_dir,
            "pin": dict(self.pin),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MountRecord":
        return cls(
            world=str(d["world"]),
            bundle_version=int(d.get("bundle_version", 1)),
            world_sha256=str(d["world_sha256"]),
            mounted_at=str(d["mounted_at"]),
            bundle_dir=str(d["bundle_dir"]),
            staged_dir=str(d["staged_dir"]),
            pin=dict(d.get("pin", {})),
        )


@dataclass
class WorldInfo:
    """A discovered World in ``lab/worlds/`` (or the out-of-folder current mount)."""

    slug: str            # manifest.world (validated) or dir name if unreadable
    display_name: str    # manifest.display_name, else slug
    path: str            # absolute path to the bundle dir
    valid: bool          # passed light validation (load_bundle), NOT a seal verify
    mounted: bool        # this slug == current_mount().world
    reason: str = ""     # when valid is False: short operator-facing why
    # Switcher swatch: {"start","end","accent","personality"} resolved through
    # the SAME validated path as the live theme (world_theme → palette_hint →
    # None). Values are validated hex / closed-enum only — safe for CSSOM.
    theme_preview: Optional[Dict[str, str]] = None
    # Short story blurb from face.json's ``tagline`` — display copy for
    # pickers/switchers. Empty string when the face has none.
    tagline: str = ""
    # Best-effort, optional. None/""/[] when the underlying data is missing
    # or malformed — never fabricated. See C2.
    term_count: Optional[int] = None
    provenance_tier: str = ""
    categories: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "path": self.path,
            "valid": self.valid,
            "mounted": self.mounted,
            "reason": self.reason,
            "theme_preview": dict(self.theme_preview) if self.theme_preview else None,
            "tagline": self.tagline,
            "term_count": self.term_count,
            "provenance_tier": self.provenance_tier,
            "categories": list(self.categories),
        }


def _catalog_extras_from_manifest_and_spec(
    manifest: Any, spec: Any
) -> tuple[Optional[int], str, List[str]]:
    """Best-effort (term_count, provenance_tier, categories) for a card (C2).

    Never raises. Each field is independently None/""/[] on missing or
    malformed data — no fabrication, no placeholder.
    """
    term_count: Optional[int] = None
    provenance_tier = ""
    categories: List[str] = []
    try:
        if isinstance(manifest, dict):
            counts = manifest.get("provenance_counts")
            if isinstance(counts, dict):
                total = counts.get("total")
                if isinstance(total, int) and not isinstance(total, bool):
                    term_count = total
            tier = manifest.get("provenance_tier")
            if isinstance(tier, str):
                provenance_tier = tier
    except Exception:  # noqa: BLE001
        pass
    try:
        if isinstance(spec, dict):
            cats = spec.get("categories")
            if isinstance(cats, list):
                for c in cats[:3]:
                    if isinstance(c, dict):
                        label = c.get("label")
                        if isinstance(label, str) and label:
                            categories.append(label)
    except Exception:  # noqa: BLE001
        pass
    return term_count, provenance_tier, categories


def _theme_preview_from_face(face: Any) -> Optional[Dict[str, str]]:
    """Swatch data for the switcher, from an (untrusted) face mapping.

    Mirrors identity.py's resolution order: validated ``theme`` block first,
    then ``palette_hint`` preset match, else None. Never raises.
    """
    try:
        if not isinstance(face, dict):
            return None
        from arail.world_theme import parse_world_theme

        spec, _reason = parse_world_theme(face.get("theme"))
        if spec is not None:
            return {
                "start": spec.dark.bg,
                "end": spec.dark.accent,
                "accent": spec.dark.accent,
                "personality": spec.personality,
            }
        hint = str(face.get("palette_hint", "")).strip()
        if hint:
            from arail.ui_theme import load_ui_theme

            preset = load_ui_theme(hint)
            if preset.id == hint or preset.env_value == hint:
                return {
                    "start": preset.preview_start,
                    "end": preset.preview_end,
                    "accent": preset.accent,
                    "personality": preset.personality,
                }
        return None
    except Exception:  # noqa: BLE001 — a broken face never breaks discovery
        return None


def _tagline_from_face(face: Any) -> str:
    """Story blurb for pickers, from an (untrusted) face mapping. Never raises."""
    try:
        if not isinstance(face, dict):
            return ""
        return str(face.get("tagline", "") or "").strip()[:200]
    except Exception:  # noqa: BLE001 — a broken face never breaks discovery
        return ""


# ── Pure (no side effects) ───────────────────────────────────────────────────


def load_bundle(bundle_dir: Path) -> Bundle:
    """Parse manifest.json then read the 6 siblings. Raises PartialBundle on any error."""
    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise PartialBundle(
            f"manifest.json missing in {bundle_dir}",
            user_message=f"Bundle missing manifest.json — is {bundle_dir} a valid WorldBundle?",
        )
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except Exception as e:
        raise PartialBundle(
            f"manifest.json unreadable: {e}",
            user_message=f"Cannot parse manifest.json: {e}",
        ) from e

    # Validate slug from manifest (path-traversal guard)
    manifest_slug = str(manifest.get("world", ""))
    if not _SLUG_RE.match(manifest_slug):
        raise SlugInvalid(
            f"Invalid world slug in manifest: {manifest_slug!r}",
            user_message=(
                f"Bundle refused: world slug {manifest_slug!r} is invalid. "
                "Slug must match ^[a-z0-9][a-z0-9-]*$ (no path separators, dots, or uppercase)."
            ),
        )

    # Read terms.json raw bytes first (needed for sha256)
    terms_path = bundle_dir / "terms.json"
    try:
        terms_raw_bytes = terms_path.read_bytes()
        terms_data = json.loads(terms_raw_bytes)
        # Ensure it has a terms list (format: {"version": 1, "terms": [...]})
        if "terms" not in terms_data:
            raise ValueError("terms.json missing 'terms' key")
    except Exception as e:
        raise PartialBundle(
            f"terms.json unreadable: {e}",
            user_message=f"Cannot read terms.json: {e}",
        ) from e

    def _read_json(name: str) -> Dict[str, Any]:
        p = bundle_dir / name
        if not p.exists():
            raise PartialBundle(
                f"{name} missing",
                user_message=f"Bundle incomplete: {name} not found in {bundle_dir}",
            )
        try:
            return json.loads(p.read_bytes())
        except Exception as e:
            raise PartialBundle(
                f"{name} unreadable: {e}",
                user_message=f"Cannot parse {name}: {e}",
            ) from e

    spec = _read_json("spec.json")

    # face.json: tolerated partial — missing/invalid → mount KB anyway
    face: Optional[Dict[str, Any]] = None
    try:
        face = _read_json("face.json")
        if not isinstance(face, dict):
            _log.warning("world_mount: face.json is not a dict; skipping face")
            face = None
    except (PartialBundle, Exception) as e:
        _log.warning("world_mount: face.json missing or invalid: %s", e)
        face = None

    # Cross-file slug consistency checks
    spec_slug = str(spec.get("slug", "")) if isinstance(spec, dict) else ""
    if spec_slug and spec_slug != manifest_slug:
        raise SlugInvalid(
            f"Slug mismatch: manifest.world={manifest_slug!r} but spec.json.slug={spec_slug!r}",
            user_message=(
                f"Bundle refused: slug disagreement between manifest ({manifest_slug!r}) "
                f"and spec.json ({spec_slug!r}). Bundle may be misassembled."
            ),
        )
    if face is not None:
        face_world = str(face.get("world", "")) if isinstance(face, dict) else ""
        if face_world and face_world != manifest_slug:
            raise SlugInvalid(
                f"Slug mismatch: manifest.world={manifest_slug!r} but face.json.world={face_world!r}",
                user_message=(
                    f"Bundle refused: slug disagreement between manifest ({manifest_slug!r}) "
                    f"and face.json ({face_world!r}). Bundle may be misassembled."
                ),
            )

    roster = _read_json("roster.json")
    drift_report = _read_json("drift-report.json")
    agenda = _read_json("agenda.json")

    return Bundle(
        bundle_dir=bundle_dir,
        manifest=manifest,
        terms_raw_bytes=terms_raw_bytes,
        terms_data=terms_data,
        spec=spec,
        face=face,
        roster=roster,
        drift_report=drift_report,
        agenda=agenda,
    )


def verify_seal(bundle: Bundle) -> SealResult:
    """sha256(raw terms bytes) == manifest.world_sha256 AND manifest.files["terms.json"].
    Also checks every other sibling's sha256 == its files{} entry."""
    computed = hashlib.sha256(bundle.terms_raw_bytes).hexdigest()
    manifest = bundle.manifest
    world_sha = str(manifest.get("world_sha256", ""))
    files_map: Dict[str, str] = manifest.get("files", {})
    files_terms_sha = str(files_map.get("terms.json", ""))

    # Check terms.json dual equality first
    terms_ok = (computed == world_sha) and (computed == files_terms_sha)

    if not terms_ok:
        msg = (
            f"Seal mismatch on terms.json:\n"
            f"  computed sha256: {computed}\n"
            f"  manifest.world_sha256: {world_sha}\n"
            f"  manifest.files['terms.json']: {files_terms_sha}\n"
            "Bundle may be corrupted or tampered."
        )
        return SealResult(
            ok=False,
            computed_sha256=computed,
            manifest_world_sha256=world_sha,
            manifest_files_sha256=files_terms_sha,
            user_message=msg,
        )

    # Check all other siblings
    for fname in _BUNDLE_FILES - {"terms.json"}:
        if fname not in files_map:
            continue
        fpath = bundle.bundle_dir / fname
        try:
            fhash = hashlib.sha256(fpath.read_bytes()).hexdigest()
        except Exception as e:
            return SealResult(
                ok=False,
                computed_sha256=computed,
                manifest_world_sha256=world_sha,
                manifest_files_sha256=files_terms_sha,
                user_message=f"Cannot read {fname} for seal check: {e}",
            )
        expected = str(files_map[fname])
        if fhash != expected:
            msg = (
                f"Seal mismatch on {fname}:\n"
                f"  computed sha256: {fhash}\n"
                f"  manifest.files['{fname}']: {expected}"
            )
            return SealResult(
                ok=False,
                computed_sha256=computed,
                manifest_world_sha256=world_sha,
                manifest_files_sha256=files_terms_sha,
                user_message=msg,
            )

    return SealResult(
        ok=True,
        computed_sha256=computed,
        manifest_world_sha256=world_sha,
        manifest_files_sha256=files_terms_sha,
        user_message="Seal OK",
    )


def check_compat(bundle: Bundle) -> None:
    """Raise SchemaSkew if bundle_schema != 1 or terms_schema != 1."""
    compat = bundle.manifest.get("compat", {})
    bundle_schema = int(compat.get("bundle_schema", 0))
    terms_schema = int(compat.get("terms_schema", 0))
    if bundle_schema != 1:
        raise SchemaSkew(
            f"Unsupported bundle_schema: {bundle_schema} (expected 1)",
            user_message=(
                f"Bundle uses schema version {bundle_schema}; ARAIL supports 1. "
                "Upgrade ARAIL or regenerate the bundle."
            ),
        )
    if terms_schema != 1:
        raise SchemaSkew(
            f"Unsupported terms_schema: {terms_schema} (expected 1)",
            user_message=(
                f"Bundle uses terms_schema {terms_schema}; ARAIL supports 1. "
                "Upgrade ARAIL or regenerate the bundle."
            ),
        )


def check_categories(bundle: Bundle) -> None:
    """Raise GateViolation if any term.category is not in spec.categories[].id."""
    declared = {c["id"] for c in bundle.categories if "id" in c}
    for term in bundle.terms:
        cat = term.get("category")
        if cat and cat not in declared:
            raise GateViolation(
                f"Term '{term.get('slug', '?')}' has undeclared category '{cat}'",
                user_message=(
                    f"Term '{term.get('term', term.get('slug', '?'))}' uses category "
                    f"'{cat}' which is not gate-produced (not in spec.categories). "
                    "This bundle may not be from a trusted DaC gate."
                ),
            )


# ── Consumer helpers ─────────────────────────────────────────────────────────


def mounted_terms(record: MountRecord) -> List[Dict[str, Any]]:
    """Return bundle terms ordered by spec.categories order then slug."""
    staged = Path(record.staged_dir)
    terms_path = staged / "terms.json"
    spec_path = staged / "spec.json"
    if not terms_path.exists():
        return []
    try:
        terms_data = json.loads(terms_path.read_bytes())
        terms_list = terms_data.get("terms", [])
    except Exception:
        return []
    try:
        spec = json.loads(spec_path.read_bytes())
        cat_order = {c["id"]: i for i, c in enumerate(spec.get("categories", []))}
    except Exception:
        cat_order = {}

    def sort_key(t: Dict[str, Any]) -> tuple:
        cat = t.get("category", "")
        return (cat_order.get(cat, 999), t.get("slug", ""))

    return sorted(terms_list, key=sort_key)


def mounted_face(record: MountRecord) -> Optional[Dict[str, Any]]:
    """Return the face.json contents from the staged dir, or None."""
    staged = Path(record.staged_dir)
    face_path = staged / "face.json"
    if not face_path.exists():
        return None
    try:
        return json.loads(face_path.read_bytes())
    except Exception:
        return None


def world_trusted_domains(record: MountRecord) -> Dict[str, Dict[str, str]]:
    """Build a holder→domain map from spec.knowledge_sources for Phase 6 curator."""
    staged = Path(record.staged_dir)
    spec_path = staged / "spec.json"
    if not spec_path.exists():
        return {}
    try:
        spec = json.loads(spec_path.read_bytes())
    except Exception:
        return {}

    # Known holder → domain mapping
    _HOLDER_DOMAIN: Dict[str, str] = {
        "NIST": "physics.nist.gov",
        "arXiv": "arxiv.org",
        "BIPM": "bipm.org",
        "CODATA": "codata.org",
        "NASA": "nasa.gov",
        "NIH": "nih.gov",
        "PubMed": "pubmed.ncbi.nlm.nih.gov",
    }

    result: Dict[str, Dict[str, str]] = {}
    for src in spec.get("knowledge_sources", []):
        holder = str(src.get("holder", "")).strip()
        if not holder:
            continue
        # Prefer known mapping; fall back to a plausible domain from the ref string
        domain = _HOLDER_DOMAIN.get(holder)
        if not domain:
            # Use the ref string if it has a URL pattern, else skip
            ref = str(src.get("ref", ""))
            # extract anything that looks like a domain
            import re
            m = re.search(r"([a-z0-9-]+\.[a-z0-9.-]+\.[a-z]{2,}|[a-z0-9-]+\.[a-z]{2,})", ref)
            if m:
                domain = m.group(1).lower()
            else:
                domain = holder.lower().replace(" ", "") + ".org"
        result[domain] = {
            "name": holder,
            "category": record.world,
        }
    return result


# ── Paths ────────────────────────────────────────────────────────────────────


def _default_data_dir() -> Path:
    from arail.config import DATA_DIR
    return DATA_DIR


def _default_pkb_root() -> Path:
    from arail.config import PKB_ROOT
    return PKB_ROOT


def _default_worlds_dir() -> Path:
    from arail.config import WORLDS_DIR
    return WORLDS_DIR


def _mount_record_path(data_dir: Path) -> Path:
    return data_dir / MOUNT_RECORD_NAME


def _staged_dir_path(pkb_root: Path, slug: str) -> Path:
    return pkb_root / "sources" / f"world-{slug}"


# ── Atomic state ─────────────────────────────────────────────────────────────


def current_mount(data_dir: Path | None = None) -> Optional[MountRecord]:
    """The single switch all consumers read. Returns None if no world is mounted."""
    dd = data_dir or _default_data_dir()
    p = _mount_record_path(dd)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_bytes())
        return MountRecord.from_dict(d)
    except Exception as e:
        _log.warning("world_mount: corrupt mount record at %s: %s", p, e)
        return None


def _write_record(record: MountRecord, data_dir: Path) -> None:
    """Atomic write of mount record via temp + os.replace."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = _mount_record_path(data_dir)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(record.to_dict(), indent=2))
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _remove_record(data_dir: Path) -> None:
    p = _mount_record_path(data_dir)
    try:
        p.unlink(missing_ok=True)
    except Exception as e:
        _log.warning("world_mount: could not remove mount record: %s", e)


def list_available_worlds(
    worlds_dir: Path | None = None,
    *,
    data_dir: Path | None = None,
) -> List[WorldInfo]:
    """Scan ``lab/worlds/`` for mountable WorldBundles. Never raises.

    Light validation only (``load_bundle`` — manifest parse, slug regex, the 6
    siblings); the full ``verify_seal`` runs at mount time, not here. Invalid
    dirs are listed with ``valid=False`` and a ``reason`` (not skipped). The
    currently-mounted World is appended even when it lives outside the folder.
    """
    wd = worlds_dir or _default_worlds_dir()
    current = current_mount(data_dir)
    current_slug = current.world if current else None

    out: List[WorldInfo] = []
    seen_slugs: set = set()
    try:
        is_dir = wd.exists() and wd.is_dir()
    except Exception:
        is_dir = False

    if is_dir:
        try:
            subdirs = sorted(
                (d for d in wd.iterdir() if d.is_dir() and not d.name.startswith(".")),
                key=lambda p: p.name,
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("world_mount: cannot scan worlds dir %s: %s", wd, e)
            subdirs = []

        for d in subdirs:
            try:
                bundle = load_bundle(d)
                slug = bundle.slug
                if slug in seen_slugs:
                    out.append(WorldInfo(
                        slug=slug,
                        display_name=str(bundle.manifest.get("display_name", slug)),
                        path=str(d.resolve()),
                        valid=False,
                        mounted=False,
                        reason=f"duplicate slug {slug}",
                    ))
                    continue
                seen_slugs.add(slug)
                term_count, provenance_tier, categories = (
                    _catalog_extras_from_manifest_and_spec(bundle.manifest, bundle.spec)
                )
                out.append(WorldInfo(
                    slug=slug,
                    display_name=str(bundle.manifest.get("display_name", slug)),
                    path=str(d.resolve()),
                    valid=True,
                    mounted=(slug == current_slug),
                    theme_preview=_theme_preview_from_face(bundle.face),
                    tagline=_tagline_from_face(bundle.face),
                    term_count=term_count,
                    provenance_tier=provenance_tier,
                    categories=categories,
                ))
            except Exception as e:  # noqa: BLE001
                out.append(WorldInfo(
                    slug=d.name,
                    display_name=d.name,
                    path=str(d.resolve()),
                    valid=False,
                    mounted=False,
                    reason=getattr(e, "user_message", str(e))[:200],
                ))

    # Order scanned worlds by display_name, case-insensitive.
    out.sort(key=lambda w: w.display_name.lower())

    # Append the currently-mounted World if it isn't already represented.
    if current_slug and not any(w.mounted for w in out):
        display = current.world
        manifest: Any = None
        spec: Any = None
        try:
            manifest = json.loads(
                (Path(current.bundle_dir) / "manifest.json").read_bytes()
            )
            display = str(manifest.get("display_name", current.world))
        except Exception:  # noqa: BLE001
            display = current.world
        try:
            spec = json.loads((Path(current.bundle_dir) / "spec.json").read_bytes())
        except Exception:  # noqa: BLE001
            spec = None
        term_count, provenance_tier, categories = (
            _catalog_extras_from_manifest_and_spec(manifest, spec)
        )
        out.append(WorldInfo(
            slug=current.world,
            display_name=display,
            path=current.bundle_dir,
            valid=True,
            mounted=True,
            reason="",
            term_count=term_count,
            provenance_tier=provenance_tier,
            categories=categories,
        ))

    return out


# ── Capability resolution sidecar (additive; NOT in the sealed mount record) ──


def _capabilities_sidecar_path(data_dir: Path) -> Path:
    return data_dir / CAPABILITIES_SIDECAR_NAME


def current_capabilities(data_dir: Path | None = None) -> List[Dict[str, Any]]:
    """Read the resolved-capabilities sidecar. Mirrors current_mount.

    Returns the list of resolved-capability dicts (possibly empty). Returns []
    if no sidecar exists or it is unreadable.
    """
    dd = data_dir or _default_data_dir()
    p = _capabilities_sidecar_path(dd)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_bytes())
        return list(d.get("capabilities", []))
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: corrupt capabilities sidecar at %s: %s", p, e)
        return []


def _resolve_and_write_capabilities(bundle_dir: Path, slug: str, data_dir: Path) -> None:
    """Best-effort: read bundle_dir/capabilities.json (seal-exempt, OPTIONAL),
    resolve against the registry, and persist to the sidecar atomically.

    Strictly additive: any failure inside here must NOT fail the mount. Three
    cases — absent (resolved=[]), malformed (resolved=[], capabilities_error
    recorded), valid (resolved per host).
    """
    cap_path = Path(bundle_dir) / "capabilities.json"
    resolved_dicts: List[Dict[str, Any]] = []
    cap_error: str | None = None

    if cap_path.exists():
        try:
            from arail.capabilities import (
                parse_capabilities_file,
                resolve_capabilities,
                MalformedCapabilities,
            )
            try:
                specs = parse_capabilities_file(cap_path)
            except MalformedCapabilities as e:
                cap_error = str(e)
                _log.warning("world_mount: malformed capabilities.json: %s", e)
            else:
                resolved = resolve_capabilities(specs)
                resolved_dicts = [r.to_dict() for r in resolved]
        except Exception as e:  # noqa: BLE001 — capabilities subsystem must never block mount
            cap_error = f"capability resolution failed: {e}"
            _log.warning("world_mount: %s", cap_error)

    # Write the sidecar atomically (temp + os.replace), same pattern as _write_record.
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "world": slug,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": resolved_dicts,
        "capabilities_error": cap_error,
    }
    target = _capabilities_sidecar_path(data_dir)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, target)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        _log.warning("world_mount: could not write capabilities sidecar: %s", e)


def _remove_capabilities_sidecar(data_dir: Path) -> None:
    p = _capabilities_sidecar_path(data_dir)
    try:
        p.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: could not remove capabilities sidecar: %s", e)


# ── World model-hint sidecar (additive; NOT sealed, NOT in _BUNDLE_FILES) ─────


def _model_sidecar_path(data_dir: Path) -> Path:
    return data_dir / MODEL_SIDECAR_NAME


def current_model_hint(data_dir: Path | None = None) -> Optional[Dict[str, Any]]:
    """Read DATA_DIR/world-model.json. Mirrors current_capabilities().

    Returns the sidecar dict, or None if absent/unreadable. Never raises.
    """
    dd = data_dir or _default_data_dir()
    p = _model_sidecar_path(dd)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_bytes())
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: corrupt model-hint sidecar at %s: %s", p, e)
        return None


def _parse_model_hint(raw: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], List[str], Optional[str]]:
    """Validate a parsed model.json. Returns (recommended, fallback, error).

    ``recommended`` is the normalized+validated dict or None (absent/invalid).
    ``fallback`` is the list of regex-valid fallback ids (invalid dropped).
    ``error`` is a human string when the file is malformed, else None.

    Defensive — model.json crosses the DaC→ARAIL repo boundary, so every field
    is treated as attacker-influenced DATA. The id is allowlist-checked; the
    rationale is length-capped; nothing here is ever shell-interpolated.
    """
    if not isinstance(raw, dict):
        return None, [], "model.json is not an object"

    schema = raw.get("schema")
    if schema != _MODEL_HINT_SCHEMA:
        # Unknown/missing schema (incl. a future /v2) → treat the whole file as
        # absent, gracefully. Never error.
        return None, [], f"unsupported or missing schema: {schema!r}"

    rec = raw.get("recommended")
    if not isinstance(rec, dict):
        return None, [], "recommended object missing or invalid"

    rid = rec.get("id")
    if not isinstance(rid, str) or not rid or not _MODEL_ID_RE.match(rid):
        return None, [], f"recommended.id missing/invalid: {rid!r}"

    normalized: Dict[str, Any] = {"id": rid}

    family = rec.get("family")
    if isinstance(family, str) and family:
        normalized["family"] = family

    size_gb = rec.get("size_gb")
    if isinstance(size_gb, (int, float)) and not isinstance(size_gb, bool):
        normalized["size_gb"] = float(size_gb)

    good_at = rec.get("good_at")
    if isinstance(good_at, list):
        normalized["good_at"] = [str(g) for g in good_at if isinstance(g, (str, int, float))]

    rationale = rec.get("rationale")
    if isinstance(rationale, str) and rationale:
        # DATA — capped, rendered escaped in a banner, NEVER prompt-injected.
        normalized["rationale"] = rationale[:_MODEL_RATIONALE_CAP]

    fallback_raw = raw.get("fallback")
    fallback: List[str] = []
    if isinstance(fallback_raw, list):
        for fid in fallback_raw:
            if isinstance(fid, str) and _MODEL_ID_RE.match(fid):
                fallback.append(fid)

    return normalized, fallback, None


def _catalog_ids() -> set:
    """Cheap catalog-membership set. Best-effort; never raises."""
    try:
        from arail.chat import load_catalog
        return {e.id for e in load_catalog()}
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: catalog unavailable for model-hint: %s", e)
        return set()


def _resolve_and_write_model_hint(bundle_dir: Path, slug: str, data_dir: Path) -> None:
    """Best-effort: read bundle_dir/model.json (seal-exempt, OPTIONAL), validate
    schema+id, do the CHEAP catalog-membership check, and persist the sidecar
    atomically. Any failure → logged, NEVER fails the mount.

    The volatile installed-vs-available distinction is NOT computed here; it is
    derived at READ time (gallery request) so a hung Ollama never slows a mount
    and the state never goes stale after the user installs the model.

    Cases mirror capabilities: absent (no sidecar written), malformed
    (model_hint_error recorded, recommended=None), valid.
    """
    model_path = Path(bundle_dir) / "model.json"
    if not model_path.exists():
        # Absent → write NO sidecar (matches "absent → none"). unmount/swap
        # remove any stale sidecar separately.
        return

    recommended: Optional[Dict[str, Any]] = None
    fallback: List[str] = []
    hint_error: Optional[str] = None
    try:
        raw = json.loads(model_path.read_bytes())
        recommended, fallback, hint_error = _parse_model_hint(raw)
    except Exception as e:  # noqa: BLE001
        hint_error = f"model.json unreadable: {e}"
        _log.warning("world_mount: %s", hint_error)

    catalog_state = "not_in_catalog"
    if recommended is not None:
        catalog_state = "in_catalog" if recommended["id"] in _catalog_ids() else "not_in_catalog"

    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "world": slug,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "recommended": recommended,
        "fallback": fallback,
        "catalog_state": catalog_state,
        "model_hint_error": hint_error,
    }
    target = _model_sidecar_path(data_dir)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, target)
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        _log.warning("world_mount: could not write model-hint sidecar: %s", e)


def _remove_model_hint_sidecar(data_dir: Path) -> None:
    p = _model_sidecar_path(data_dir)
    try:
        p.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: could not remove model-hint sidecar: %s", e)


def _stage_files(bundle: Bundle, pkb_root: Path) -> Path:
    """Stage the 6 bundle files to pkb/sources/world-<slug>/ via a .staging dir.

    Uses atomic dir rename: .staging-<slug>/ → world-<slug>/
    Returns the final staged dir path.
    """
    slug = bundle.slug
    final_dir = _staged_dir_path(pkb_root, slug)
    staging_dir = pkb_root / "sources" / f".staging-{slug}"

    # Clean up any leftover staging dir
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    staging_dir.mkdir(parents=True, exist_ok=True)

    # Copy the 6 sibling files
    for fname in _BUNDLE_FILES:
        src = bundle.bundle_dir / fname
        if src.exists():
            shutil.copy2(src, staging_dir / fname)

    # NEW (additive, best-effort): copy SKILL.md if present.
    # SKILL.md is seal-EXEMPT — NOT in _BUNDLE_FILES; a missing or unreadable
    # SKILL.md never blocks the mount. The copy lives in the staged dir and is
    # read at compose time by skills_loader.load_world_skill() as untrusted DATA.
    src_skill = bundle.bundle_dir / _WORLD_SKILL_NAME
    if src_skill.exists():
        try:
            shutil.copy2(src_skill, staging_dir / _WORLD_SKILL_NAME)
        except Exception as e:
            _log.warning(
                "world_mount: SKILL.md stage failed (continuing): %s", e
            )

    # Emit world-<slug>.md index page + one wiki-ready page per term.
    _write_index_page(bundle, staging_dir)
    _write_term_pages(bundle, staging_dir)

    # Swap with the shortest possible "absent" window: step the live dir aside
    # (one rename), bring the new dir live (one rename) — two back-to-back
    # metadata ops — then delete the old aside AFTER the new dir is live, so the
    # slow rmtree never blocks consumers reading mounted_terms during a same-slug
    # swap. On failure, restore the old dir (never leave the World unmounted).
    old_aside = pkb_root / "sources" / f".old-{slug}"
    if old_aside.exists():
        shutil.rmtree(old_aside)
    had_old = final_dir.exists()
    if had_old:
        final_dir.rename(old_aside)
    try:
        staging_dir.rename(final_dir)
    except Exception:
        if had_old and old_aside.exists() and not final_dir.exists():
            old_aside.rename(final_dir)  # roll back to the previous World
        raise
    if had_old and old_aside.exists():
        shutil.rmtree(old_aside)
    return final_dir


def _write_index_page(bundle: Bundle, dest_dir: Path) -> None:
    """Write a world-<slug>.md human-browsable index + wiki node."""
    slug = bundle.slug
    display = bundle.manifest.get("display_name", slug)
    lines = [
        f"# {display}",
        "",
        f"**World:** `{slug}` · **bundle_version:** {bundle.bundle_version}",
        "",
        "## Terms",
        "",
        "| Term | Short definition | Source |",
        "|------|-----------------|--------|",
    ]
    for term in bundle.terms:
        t = term.get("term", "")
        s = term.get("short", "").replace("|", "\\|")
        src = term.get("source", "").replace("|", "\\|")
        lines.append(f"| {t} | {s} | {src} |")
    lines.append("")
    content = "\n".join(lines)
    (dest_dir / f"world-{slug}.md").write_text(content, encoding="utf-8")


    # Term-field sanitizers. All term content is untrusted DATA — a hostile
    # author can seal anything. These reuse world_forge's skill sanitizers
    # (newline collapse + ZWNJ-neutralized leading control token) so a term
    # can never forge frontmatter, headings, list items or wikilink targets.


_TERM_SLUG_SAFE_RE = re.compile(r"[^a-z0-9-]+")
_TAG_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")
_ALIAS_UNSAFE_RE = re.compile(r"[\[\],\"'\r\n]+")


def _safe_term_slug(raw: Any) -> str:
    """Reduce an untrusted slug/related target to [a-z0-9-]. May return ''."""
    return _TERM_SLUG_SAFE_RE.sub("-", str(raw).lower()).strip("-")[:80]


def _safe_tag(raw: Any) -> str:
    return _TAG_SAFE_RE.sub("-", str(raw)).strip("-")[:60]


def _safe_alias(raw: Any) -> str:
    """Alias list items live inside a [a, b] frontmatter literal — strip the
    characters that could break out of it."""
    flat = _ALIAS_UNSAFE_RE.sub(" ", str(raw))
    return re.sub(r"\s+", " ", flat).strip()[:80]


def _sanitize_inline(value: Any, cap: int = 4000) -> str:
    """Flatten an untrusted term field to one safe markdown line."""
    try:
        from arail.world_forge import sanitize_body_field
        return sanitize_body_field(str(value))[:cap]
    except Exception:  # noqa: BLE001 — replicate the ~5-line sanitizer
        flat = re.sub(r"[\r\n]+", " ", str(value)).strip()
        return re.sub(r"^([#\->`])", "‌\\1", flat)[:cap]


def _fm_scalar(value: Any, cap: int = 300) -> str:
    """Frontmatter scalar: newline-collapsed, double-quoted, escaped."""
    try:
        from arail.world_forge import sanitize_frontmatter_scalar
        return sanitize_frontmatter_scalar(str(value)[:cap])
    except Exception:  # noqa: BLE001
        flat = re.sub(r"[\r\n]+", " ", str(value)[:cap]).strip()
        return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_term_pages(bundle: Bundle, dest_dir: Path) -> None:
    """Write one wiki-ready markdown page per term to ``dest_dir/terms/``.

    THIS is what populates the Knowledge Base: the wiki indexes each page
    (frontmatter title/tags/aliases feed search), the ``[[related]]``
    wikilinks become knowledge-graph edges, and LanceDB embeds each page
    individually instead of one hashed terms.json blob. The term's own slug
    is emitted as an alias so bare ``[[<term-slug>]]`` targets resolve
    through wiki.resolve_links' alias lookup. Never raises.
    """
    terms = [t for t in bundle.terms if isinstance(t, dict)]
    if not terms:
        return
    world_slug = bundle.slug
    terms_dir = dest_dir / "terms"
    terms_dir.mkdir(parents=True, exist_ok=True)
    for term in terms:
        slug = _safe_term_slug(term.get("slug", ""))
        if not slug:
            continue
        tags = [f"world-{world_slug}"]
        category = _safe_tag(term.get("category", ""))
        if category:
            tags.append(category)
        aliases: List[str] = [slug]
        aka = term.get("aka")
        if isinstance(aka, list):
            for a in aka[:8]:
                clean = _safe_alias(a)
                if clean and clean not in aliases:
                    aliases.append(clean)

        lines = [
            "---",
            f"title: {_fm_scalar(term.get('term') or slug)}",
            f"tags: [{', '.join(tags)}]",
            f"aliases: [{', '.join(aliases)}]",
            "---",
            "",
        ]
        short = _sanitize_inline(term.get("short", ""), cap=500)
        if short:
            lines += [short, ""]
        definition = _sanitize_inline(term.get("definition", ""), cap=4000)
        if definition:
            lines += [definition, ""]
        example = _sanitize_inline(term.get("example", ""), cap=1000)
        if example:
            lines += [f"**Example:** {example}", ""]

        related = term.get("related")
        rel_slugs: List[str] = []
        if isinstance(related, list):
            for r in related[:32]:
                rs = _safe_term_slug(r)
                if rs and rs != slug and rs not in rel_slugs:
                    rel_slugs.append(rs)
        if rel_slugs:
            lines += ["## Related", ""]
            lines += [f"- [[{rs}]]" for rs in rel_slugs]
            lines.append("")

        source = _sanitize_inline(term.get("source", ""), cap=500)
        if source:
            lines += [f"Source: {source}", ""]
        try:
            (terms_dir / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — one bad term never blocks the mount
            _log.warning("world_mount: term page write failed for %s: %s", slug, e)


def _index_staged(staged_dir: Path, pkb_root: Path) -> str:
    """Enqueue every staged KB-surface file for LanceDB indexing.

    Returns a status string (never raises):
      - ``"indexed"``     — ensure_ready ran, KB-surface files were enqueued
      - ``"unavailable"`` — lancedb is not installed (keyword search still works)
      - ``"error: …"``    — unexpected failure, logged

    Machinery files (see is_world_machinery_path) are skipped; term pages
    under terms/ are included via the recursive walk.
    """
    try:
        from arail.vector_index import available
        if not available():
            _log.warning(
                "world_mount: lancedb not installed — semantic index skipped "
                "(keyword search still works)"
            )
            return "unavailable"
        from arail.pkb_index import ensure_ready, schedule_upsert
        ensure_ready(pkb_root)
        for p in sorted(staged_dir.rglob("*")):
            if not p.is_file() or is_world_machinery_path(p):
                continue
            try:
                schedule_upsert(p, pkb_root=pkb_root)
            except Exception as e:
                _log.warning("world_mount: schedule_upsert failed for %s: %s", p, e)
        return "indexed"
    except Exception as e:  # noqa: BLE001 — indexing must never fail a mount
        _log.warning("world_mount: indexer unavailable: %s", e)
        return f"error: {e}"


def _emit_index_status(world: str, status: str) -> None:
    """Surface a non-"indexed" semantic-index outcome on the activity stream.

    Best-effort; the mount already succeeded by the time this runs.
    """
    try:
        from arail.activity import activity_log
        if status == "unavailable":
            activity_log.emit(
                "world",
                "Semantic search index unavailable (lancedb not installed) — "
                "keyword search still works.",
                "warn",
            )
        elif status.startswith("error"):
            activity_log.emit(
                "world",
                f"World '{world}' KB indexing issue: {status}",
                "warn",
            )
    except Exception:  # noqa: BLE001
        pass


def _prune_swept_approvals(pkb_root: Path) -> int:
    """Reconcile the Compiled KB with the files the sweep just deleted.

    ``_sweep_other_worlds()`` removes the previous World's staged term files,
    but the approval manifest is a set of POINTERS to those paths — nothing
    else prunes it. Left alone, the approvals outlive their files, and since
    the retrieval gate intersects approved paths with live search hits, every
    one of them silently matches nothing. A lab that had switched Worlds a
    couple of times could reach the state where 554 of 556 approvals were
    corpses and ``search_for_agents()`` returned zero hits for ANY query,
    in any World, with no error anywhere to explain it.

    Returns the number pruned. Never raises — a KB bookkeeping failure must
    not be able to fail a mount that has otherwise succeeded.
    """
    try:
        from arail import compiled_kb
        dropped = compiled_kb.prune_dangling(pkb_root)
        if dropped:
            _log.info(
                "world_mount: pruned %d approval(s) whose raw file was swept "
                "(Compiled KB reconciled with disk)", len(dropped)
            )
        return len(dropped)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: compiled-KB prune skipped: %s", e)
        return 0


def _switch_goal_for_world(new_world: str | None, data_dir: Path) -> None:
    """Archive the current goal if it belongs to a different World than
    the one now mounted — the same "the lab reflects the mounted World"
    rule #163 already applies to the Compiled-KB gate, applied to goals.

    goals.py has no data_dir parameterization (GoalStore always operates
    on the process's own arail.config.DATA_DIR, unlike world_mount's own
    functions which all accept an explicit override for tests/CLI
    isolation) — so this must ONLY fire when ``data_dir`` resolved to the
    process's REAL default. A test that mounts a fake bundle into an
    isolated tmp_path data_dir must never reach through to the actual
    live goal_store; skipping here (rather than in goals.py) keeps that
    guard next to the mismatch it exists to prevent, not buried in a
    module that has no idea it's being called from a sandboxed test.

    Never raises — goal-store bookkeeping must not be able to fail a
    mount/swap/unmount that has otherwise succeeded.
    """
    try:
        if data_dir != _default_data_dir():
            return
        from arail.goals import GoalStore
        archived = GoalStore().archive_if_world_mismatch(new_world)
        if archived:
            _log.info(
                "world_mount: archived the current goal — it belonged to a "
                "different World than the one now mounted (%s)", new_world
            )
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: goal-switch reconciliation skipped: %s", e)


def _refresh_kb_surfaces(pkb_root: Path) -> None:
    """Make a mount/swap/unmount visible in the KB immediately.

    Reconciles the Compiled KB against disk first (the sweep that runs just
    before this deletes files the approval manifest still points at), then
    forces the debounced pkb_index queue to flush now, then rebuilds the
    wiki — via the async debouncer when an event loop is running, otherwise
    (CLI mounts) synchronously best-effort. Never raises.
    """
    _prune_swept_approvals(pkb_root)
    try:
        from arail.pkb_index import flush_now
        flush_now()
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: pkb_index flush failed: %s", e)
    try:
        from arail import wiki
        scheduled = wiki.schedule_rebuild(pkb_root=pkb_root)
        if not scheduled and wiki._auto_rebuild_enabled():
            # schedule_rebuild returns False with no running loop (CLI mount)
            # — build synchronously so the wiki + knowledge graph aren't
            # silently stale until the next portal boot.
            try:
                wiki.compile_wiki(pkb_root)
            except Exception as e:  # noqa: BLE001
                _log.warning("world_mount: synchronous wiki build failed: %s", e)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: wiki refresh skipped: %s", e)


def _sweep_other_worlds(pkb_root: Path, keep_slug: str) -> int:
    """Reset the World-dataset layer of the KB to *only* the current World.

    A World IS the lab's dataset: mounting/switching one makes the Knowledge
    Base reflect that World's terms, not an accumulation of every World ever
    mounted. So on mount/swap we remove every other ``sources/world-*/`` staged
    dir, leaving ``world-<keep_slug>`` plus all NON-world content (the user's
    own ingested docs/notes under sources/, research/, notes/…) untouched.

    Returns the number of stale World dirs removed. Never raises.
    """
    removed = 0
    try:
        sources = pkb_root / "sources"
        if not sources.is_dir():
            return 0
        keep = f"world-{keep_slug}"
        for child in sources.iterdir():
            if (child.is_dir() and child.name.startswith("world-")
                    and child.name != keep):
                try:
                    shutil.rmtree(child)
                    removed += 1
                    # Its stale LanceDB rows are pruned automatically on the
                    # next flush (_flush drops rows whose files no longer exist).
                except Exception as e:  # noqa: BLE001
                    _log.warning("world_mount: could not sweep %s: %s", child.name, e)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: world sweep skipped: %s", e)
    return removed


def _adopt_into_catalog(
    bundle_dir: Path,
    slug: str,
    worlds_dir: Path | None = None,
) -> Optional[Path]:
    """Copy a freshly-mounted bundle into ``WORLDS_DIR/<slug>/`` so it persists
    in the switcher catalog and stays re-mountable after unmount.

    The portal's World selection is path-jailed to ``WORLDS_DIR`` (this lab runs
    on other people's machines — see ``_resolve_world_dir``). A World mounted
    from an *external* path (CLI ``world mount <dir>``, a DaC export elsewhere)
    is therefore visible only while mounted and can never be re-selected once
    unmounted. Adopting a byte-for-byte copy under ``WORLDS_DIR`` makes it a
    first-class catalog entry the jailed slug path can re-mount — and the copy
    is identical, so ``verify_seal`` still passes.

    Best-effort: never raises (a failed adopt must not fail the mount). No-op
    when the bundle already lives under ``WORLDS_DIR`` (slug-select already
    resolves it) or when the slug is invalid.
    """
    try:
        wd = (worlds_dir or _default_worlds_dir())
        src = bundle_dir.resolve()
        wd_res = wd.resolve()
        # Already inside the catalog jail → nothing to adopt.
        src_s, root_s = str(src), str(wd_res)
        if src_s == root_s or src_s.startswith(root_s + os.sep):
            return None
        if not _SLUG_RE.match(slug):
            return None
        wd.mkdir(parents=True, exist_ok=True)
        dest = wd / slug
        # Stage into a hidden temp dir (skipped by the catalog scan), then swap
        # atomically so a crash mid-copy never leaves a half-written entry.
        tmp = wd / f".adopting-{slug}"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for f in sorted(src.iterdir()):
            if f.is_file():
                shutil.copy2(f, tmp / f.name)
        if dest.exists():
            old = wd / f".old-{slug}"
            shutil.rmtree(old, ignore_errors=True)
            dest.rename(old)
            tmp.rename(dest)
            shutil.rmtree(old, ignore_errors=True)
        else:
            tmp.rename(dest)
        return dest
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: catalog adopt failed (continuing): %s", e)
        return None


# ── Public state-mutating API ─────────────────────────────────────────────────


def mount(
    bundle_dir: Path,
    *,
    pkb_root: Path | None = None,
    data_dir: Path | None = None,
    worlds_dir: Path | None = None,
) -> MountRecord:
    """Mount a WorldBundle. Atomic: refuses before touching disk on any error.

    Identity (name/logo/theme/intent/framing) flips live from the mount sidecar
    at request time via ``arail.identity`` — mounting writes NO ``.env`` and
    requires NO restart.

    Ordering:
    1. load+verify+compat+categories in-memory
    2. Stage files to pkb/sources/world-<slug>/ via .staging-<slug>/
    3. ensure_ready + schedule_upsert per staged file
    4. Write pointer LAST (atomic temp+replace)
    3.5. Auto-approve this World's term pages into the Compiled KB
         (best-effort, QA-6 — see auto_approve_world_terms)
    5. wiki.schedule_rebuild() best-effort
    """
    bundle_dir = Path(bundle_dir)
    pkb = pkb_root or _default_pkb_root()
    dd = data_dir or _default_data_dir()

    # Step 1: load + verify (refuse before touching disk)
    bundle = load_bundle(bundle_dir)
    seal = verify_seal(bundle)
    if not seal.ok:
        raise SealMismatch(seal.user_message, user_message=seal.user_message)
    check_compat(bundle)
    check_categories(bundle)

    # Step 2: stage files
    staged_dir = _stage_files(bundle, pkb)

    # Step 2b: World = dataset — reset the KB's World layer to only this World.
    _sweep_other_worlds(pkb, bundle.slug)

    # Step 3: index (best-effort; LanceDB-absent must not abort mount)
    try:
        status = _index_staged(staged_dir, pkb)
        _emit_index_status(bundle.world, status)
    except Exception as e:
        _log.warning("world_mount: indexing failed (continuing): %s", e)

    # Step 4: write pointer LAST
    now = datetime.now(timezone.utc).isoformat()
    record = MountRecord(
        world=bundle.world,
        bundle_version=bundle.bundle_version,
        world_sha256=seal.computed_sha256,
        mounted_at=now,
        bundle_dir=str(bundle_dir.resolve()),
        staged_dir=str(staged_dir),
        pin={"world_sha256": seal.computed_sha256},
    )
    _write_record(record, dd)

    # Step 3.5: narrowly-scoped auto-approval of this World's term pages,
    # reconciled against the just-verified bundle's terms.json (QA-6). A
    # path is admitted iff it matches sources/world-<slug>/terms/<term-
    # slug>.md AND <term-slug> is in bundle.terms. Best-effort — an
    # exception here must never fail the mount.
    try:
        from arail.compiled_kb import auto_approve_world_terms
        auto_approve_world_terms(
            bundle.slug, bundle_terms=bundle.terms,
            seal_sha=seal.computed_sha256, pkb_root=pkb)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: auto-approval skipped (continuing): %s", e)

    # Step 4b: the lab now reflects a different World than whatever goal
    # was running (if any) — same "reflects the mounted World" rule KB
    # approvals already follow. Archives, never blocks the mount.
    _switch_goal_for_world(bundle.slug, dd)

    # Step 5: adopt into the catalog so the switcher keeps the World after
    # unmount (re-mountable via the jailed slug path). Best-effort; no-op when
    # the bundle already lives under WORLDS_DIR.
    _adopt_into_catalog(bundle_dir, bundle.world, worlds_dir)

    # Step 6: make it visible in the KB now (flush index + rebuild wiki;
    # CLI mounts with no loop build synchronously).
    _refresh_kb_surfaces(pkb)

    # Step 7: resolve declared capabilities → sidecar (best-effort, never fails mount)
    try:
        _resolve_and_write_capabilities(bundle_dir, bundle.slug, dd)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: capability resolution skipped: %s", e)

    # Step 8: resolve declared model hint → sidecar (best-effort, never fails mount)
    try:
        _resolve_and_write_model_hint(bundle_dir, bundle.slug, dd)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: model-hint resolution skipped: %s", e)

    return record


def unmount(
    *,
    data_dir: Path | None = None,
    pkb_root: Path | None = None,
    remove_staged: bool = False,
) -> bool:
    """Remove the mount pointer (first), optionally clean the staged dir.

    Returns True if a World was mounted, False if already unmounted.
    """
    dd = data_dir or _default_data_dir()
    record = current_mount(dd)
    if record is None:
        return False

    # Remove pointer FIRST (atomic ordering)
    _remove_record(dd)
    # Remove the capabilities sidecar alongside the pointer.
    _remove_capabilities_sidecar(dd)
    # Remove the model-hint sidecar alongside the pointer (idempotent).
    _remove_model_hint_sidecar(dd)

    # The lab now reflects NO World — unconditionally, not gated on
    # remove_staged: a goal set while a World was mounted must not keep
    # "running" once that World is gone, whether or not its staged files
    # were also removed.
    _switch_goal_for_world(None, dd)

    if remove_staged:
        staged = Path(record.staged_dir)
        if staged.exists():
            try:
                shutil.rmtree(staged)
            except Exception as e:
                _log.warning("world_mount: could not remove staged dir %s: %s", staged, e)
        # This branch deletes raw files exactly like the mount-time sweep
        # does, so it owes the approval manifest the same reconciliation.
        # unmount() does NOT go through _refresh_kb_surfaces() (it only
        # schedules a wiki rebuild), so the call has to be made here or the
        # unmount path leaves dangling approvals the mount path would not.
        _prune_swept_approvals(pkb_root or _default_pkb_root())

    # wiki rebuild best-effort
    try:
        from arail import wiki
        wiki.schedule_rebuild()
    except Exception:
        pass

    return True


def swap(
    new_dir: Path,
    *,
    pkb_root: Path | None = None,
    data_dir: Path | None = None,
) -> MountRecord:
    """Stage + verify new bundle, then flip pointer. Old world stays on failure.

    Identity flips live from the new sidecar — no ``.env`` write, no restart.
    """
    new_dir = Path(new_dir)
    pkb = pkb_root or _default_pkb_root()
    dd = data_dir or _default_data_dir()

    # Verify new bundle before touching anything
    bundle = load_bundle(new_dir)
    seal = verify_seal(bundle)
    if not seal.ok:
        raise SealMismatch(seal.user_message, user_message=seal.user_message)
    check_compat(bundle)
    check_categories(bundle)

    # Stage new + reset the World-dataset layer to only this World.
    staged_dir = _stage_files(bundle, pkb)
    _sweep_other_worlds(pkb, bundle.slug)
    try:
        status = _index_staged(staged_dir, pkb)
        _emit_index_status(bundle.world, status)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: swap indexing failed (continuing): %s", e)

    # Flip pointer (write new record over the old one)
    now = datetime.now(timezone.utc).isoformat()
    record = MountRecord(
        world=bundle.world,
        bundle_version=bundle.bundle_version,
        world_sha256=seal.computed_sha256,
        mounted_at=now,
        bundle_dir=str(new_dir.resolve()),
        staged_dir=str(staged_dir),
        pin={"world_sha256": seal.computed_sha256},
    )
    _write_record(record, dd)

    # Step 4.5: narrowly-scoped auto-approval of the incoming World's term
    # pages, same as mount()'s step 3.5 (QA-6 review, BLOCK-1) — swap() is
    # the path the operator actually takes on every World switch, so it
    # needs the same reconciliation against the just-verified bundle's
    # terms.json. Best-effort — an exception here must never fail the swap.
    try:
        from arail.compiled_kb import auto_approve_world_terms
        auto_approve_world_terms(
            bundle.slug, bundle_terms=bundle.terms,
            seal_sha=seal.computed_sha256, pkb_root=pkb)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: auto-approval skipped (continuing): %s", e)

    # The lab now reflects a different World — same rule mount() follows.
    _switch_goal_for_world(bundle.slug, dd)

    _refresh_kb_surfaces(pkb)

    # Resolve declared capabilities → sidecar (best-effort, never fails swap)
    try:
        _resolve_and_write_capabilities(new_dir, bundle.slug, dd)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: capability resolution skipped: %s", e)

    # Re-resolve the model hint for the NEW World. Clear the old sidecar first so
    # swapping to a World with no model.json doesn't leave a stale hint behind
    # (the resolver writes nothing when model.json is absent).
    try:
        _remove_model_hint_sidecar(dd)
        _resolve_and_write_model_hint(new_dir, bundle.slug, dd)
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: model-hint resolution skipped: %s", e)

    return record


# ── Dictionary integration ────────────────────────────────────────────────────


def term_to_dict_entry(term: Dict[str, Any]) -> Dict[str, Any]:
    """Map a DaC term node → dictionary TermEntry-compatible dict.

    Mapping (DaC field → TermEntry field):
      slug   → key
      term   → term
      short  → short_def
      example → examples (wrapped in list)
      source  → origin
      related → related
      category → category (passthrough, not in TermEntry but stored)
    """
    from arail.dictionary import _MAX_SHORT_DEF, _MAX_EXAMPLE, _MAX_RELATED, _MAX_SLUG

    key = str(term.get("slug", ""))[:_MAX_SLUG]
    t = str(term.get("term", key))
    short = str(term.get("short", ""))[:_MAX_SHORT_DEF]
    example = str(term.get("example", ""))
    examples = [example[:_MAX_EXAMPLE]] if example else []
    origin = str(term.get("source", ""))
    related = list(term.get("related", []))[:_MAX_RELATED]
    category = str(term.get("category", ""))

    return {
        "term": t,
        "short_def": short,
        "examples": examples,
        "origin": origin,
        "related": related,
        "key": key,
        "created_at": "",
        "category": category,
        "can_generate": False,
    }


def get_mounted_dict_terms(record: MountRecord) -> List[Dict[str, Any]]:
    """Return terms mapped to TermEntry-compatible dicts, ordered per spec."""
    raw = mounted_terms(record)
    return [term_to_dict_entry(t) for t in raw]


# ── Shipped-bundle verification (vendored qukaizen-dac exports) ──────────────


def verify_shipped_worlds(worlds_dir: Path | None = None) -> List[Dict[str, Any]]:
    """Run the full verify ladder over every bundle dir in the catalog.

    Shipped Worlds are sealed qukaizen-dac exports committed into the repo;
    this is the single-repo-install integrity check. Never raises. Returns one
    result dict per bundle dir: {"slug", "path", "ok", "reason", "terms",
    "seal"} — ``slug`` falls back to the dir name when the manifest is
    unreadable.
    """
    wd = worlds_dir or _default_worlds_dir()
    results: List[Dict[str, Any]] = []
    try:
        subdirs = sorted(
            (d for d in wd.iterdir() if d.is_dir() and not d.name.startswith(".")),
            key=lambda p: p.name,
        ) if wd.exists() and wd.is_dir() else []
    except Exception as e:  # noqa: BLE001
        _log.warning("world_mount: cannot scan worlds dir %s: %s", wd, e)
        subdirs = []

    for d in subdirs:
        res: Dict[str, Any] = {
            "slug": d.name, "path": str(d.resolve()),
            "ok": False, "reason": "", "terms": 0, "seal": "",
        }
        try:
            bundle = load_bundle(d)
            res["slug"] = bundle.slug
            res["terms"] = len(bundle.terms)
            seal = verify_seal(bundle)
            if not seal.ok:
                res["reason"] = seal.user_message
            else:
                res["seal"] = seal.computed_sha256
                check_compat(bundle)
                check_categories(bundle)
                res["ok"] = True
        except Exception as e:  # noqa: BLE001 — report, never raise
            res["reason"] = getattr(e, "user_message", str(e))[:300]
        results.append(res)
    return results


# ── __main__ CLI ──────────────────────────────────────────────────────────────


_VERIFY_SHIPPED_REMEDY = (
    "Shipped bundles are committed to git — restore with "
    "`git checkout -- lab/worlds/<slug>`. Do not hand-edit sealed files; "
    "resealing lives upstream in qukaizen-dac (portal term edits reseal "
    "properly on their own)."
)


def _cmd_verify_shipped(args: argparse.Namespace) -> int:
    """Verify every vendored bundle in the catalog (and optionally examples/)."""
    dirs: List[Path] = [_default_worlds_dir()]
    if getattr(args, "examples", False):
        dirs.append(Path("examples/worlds"))

    all_ok = True
    any_seen = False
    for wd in dirs:
        for res in verify_shipped_worlds(wd):
            any_seen = True
            if res["ok"]:
                print(f"OK   world={res['slug']!r} terms={res['terms']} seal={res['seal'][:16]}… ({res['path']})")
            else:
                all_ok = False
                print(f"FAIL world={res['slug']!r} — {res['reason']} ({res['path']})", file=sys.stderr)
    if not any_seen:
        print("No World bundles found to verify.", file=sys.stderr)
        return 2
    if not all_ok:
        print(_VERIFY_SHIPPED_REMEDY, file=sys.stderr)
        return 2
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle_dir)
    try:
        bundle = load_bundle(bundle_dir)
    except PartialBundle as e:
        print(f"ERROR: {e.user_message}", file=sys.stderr)
        return 1

    seal = verify_seal(bundle)
    if not seal.ok:
        print(f"ERROR: {seal.user_message}", file=sys.stderr)
        return 2

    try:
        check_compat(bundle)
    except SchemaSkew as e:
        print(f"ERROR: {e.user_message}", file=sys.stderr)
        return 3

    try:
        check_categories(bundle)
    except GateViolation as e:
        print(f"ERROR: {e.user_message}", file=sys.stderr)
        return 4

    print(f"OK: world={bundle.world!r} bundle_version={bundle.bundle_version} terms={len(bundle.terms)} seal={seal.computed_sha256[:16]}…")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir) if getattr(args, "data_dir", None) else _default_data_dir()
    record = current_mount(data_dir)
    if record is None:
        print("No world currently mounted.")
        return 0
    print(f"Mounted world: {record.world!r}")
    print(f"  bundle_version: {record.bundle_version}")
    print(f"  world_sha256:   {record.world_sha256[:16]}…")
    print(f"  mounted_at:     {record.mounted_at}")
    print(f"  staged_dir:     {record.staged_dir}")
    return 0


def _cmd_mount(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle_dir)
    data_dir = Path(args.data_dir) if getattr(args, "data_dir", None) else _default_data_dir()
    pkb_root = Path(args.pkb_root) if getattr(args, "pkb_root", None) else _default_pkb_root()

    try:
        record = mount(
            bundle_dir,
            pkb_root=pkb_root,
            data_dir=data_dir,
        )
    except SealMismatch as e:
        print(f"ERROR (seal mismatch): {e.user_message}", file=sys.stderr)
        return 2
    except PartialBundle as e:
        print(f"ERROR (partial bundle): {e.user_message}", file=sys.stderr)
        return 1
    except SchemaSkew as e:
        print(f"ERROR (schema skew): {e.user_message}", file=sys.stderr)
        return 3
    except GateViolation as e:
        print(f"ERROR (gate violation): {e.user_message}", file=sys.stderr)
        return 4
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 5

    print(f"Mounted world={record.world!r} at {record.mounted_at}")
    print(f"  staged_dir: {record.staged_dir}")
    print("")
    print("Lab identity (name, theme, intent, framing) flips live from this mount.")
    print("No restart needed; unmount reverts to the operator brand.")
    return 0


def _cmd_unmount(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir) if getattr(args, "data_dir", None) else _default_data_dir()
    pkb_root = Path(args.pkb_root) if getattr(args, "pkb_root", None) else _default_pkb_root()
    remove_staged = getattr(args, "remove_staged", False)
    was_mounted = unmount(data_dir=data_dir, pkb_root=pkb_root, remove_staged=remove_staged)
    if was_mounted:
        print("World unmounted.")
    else:
        print("No world was mounted.")
    return 0


def _cmd_swap(args: argparse.Namespace) -> int:
    new_dir = Path(args.bundle_dir)
    data_dir = Path(args.data_dir) if getattr(args, "data_dir", None) else _default_data_dir()
    pkb_root = Path(args.pkb_root) if getattr(args, "pkb_root", None) else _default_pkb_root()
    try:
        record = swap(new_dir, pkb_root=pkb_root, data_dir=data_dir)
    except (SealMismatch, PartialBundle, SchemaSkew, GateViolation, Exception) as e:
        um = getattr(e, "user_message", str(e))
        print(f"ERROR: {um}", file=sys.stderr)
        return 2
    print(f"Swapped to world={record.world!r} at {record.mounted_at}")
    print("Lab identity flips live from the new mount. No restart needed.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m arail.world_mount",
        description="ARAIL World Mount — manage a mounted DaC WorldBundle.",
    )
    parser.add_argument("--data-dir", dest="data_dir", default=None, help="Override DATA_DIR")
    parser.add_argument("--pkb-root", dest="pkb_root", default=None, help="Override PKB_ROOT")

    sub = parser.add_subparsers(dest="command")

    # verify
    p_verify = sub.add_parser("verify", help="Verify bundle seal + compat + categories")
    p_verify.add_argument("bundle_dir", help="Path to the WorldBundle directory")

    # verify-shipped
    p_vs = sub.add_parser(
        "verify-shipped",
        help="Verify every vendored bundle in lab/worlds/ (sealed qukaizen-dac exports)",
    )
    p_vs.add_argument("--examples", action="store_true",
                      help="Also verify the demo bundles in examples/worlds/")

    # list
    sub.add_parser("list", help="Show currently mounted world")

    # mount
    p_mount = sub.add_parser(
        "mount",
        help="Mount a WorldBundle (lab identity flips live; no .env write, no restart)",
    )
    p_mount.add_argument("bundle_dir", help="Path to the WorldBundle directory")

    # unmount
    p_unmount = sub.add_parser("unmount", help="Unmount the current world")
    p_unmount.add_argument("--remove-staged", action="store_true", dest="remove_staged",
                           help="Also remove the staged files from pkb/sources/")

    # swap
    p_swap = sub.add_parser("swap", help="Swap to a new WorldBundle (keeps old on failure)")
    p_swap.add_argument("bundle_dir", help="Path to the new WorldBundle directory")

    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "verify":
        sys.exit(_cmd_verify(args))
    elif args.command == "verify-shipped":
        sys.exit(_cmd_verify_shipped(args))
    elif args.command == "list":
        sys.exit(_cmd_list(args))
    elif args.command == "mount":
        sys.exit(_cmd_mount(args))
    elif args.command == "unmount":
        sys.exit(_cmd_unmount(args))
    elif args.command == "swap":
        sys.exit(_cmd_swap(args))
    else:
        parser.print_help()
        sys.exit(0)
