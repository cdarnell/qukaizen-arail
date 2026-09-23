"""``arailctl nucleus stage`` — snapshot writer (ARCHITECTURE.md §3 N5, §4.8).

Only reads local paths the operator passes on the CLI. Never fetches —
not even ``git fetch`` (the git adapter runs ``git log`` only). Acquisition
(cloning a repo, downloading a mapping file) is the operator's explicit,
networked, pre-run step, entirely outside this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from arail.nucleus.corpus.sources import cve_vulns, git_kernel
from arail.nucleus.errors import DomainConfigError

_MANIFEST_SCHEMA = "nucleus.corpus-manifest/v1"

_ADAPTERS = {"git", "cve"}   # kinds with a real extractor this sprint
_KNOWN_KINDS = {"git", "cve", "lkml", "lwn", "world"}  # schema-reserved, some absent


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


@dataclass(frozen=True)
class StageResult:
    manifest: dict
    manifest_sha256: str
    items_path: Path
    manifest_path: Path
    n_items: int


def _corpus_dir(domain_name: str, *, nucleus_data: Optional[Path] = None) -> Path:
    if nucleus_data is None:
        from arail.nucleus.paths import nucleus_data as _nd

        nucleus_data = _nd()
    return Path(nucleus_data) / "corpus" / domain_name


def stage(
    domain,
    *,
    provided_sources: Dict[str, Path],
    since: Optional[str] = None,
    max_commits: Optional[int] = None,
    nucleus_data: Optional[Path] = None,
) -> StageResult:
    """``domain`` is a domain.DomainConfig (its ``corpus_sources`` tuple of
    ``(kind, name)`` drives what's expected). ``provided_sources`` maps
    ``name -> local path`` for whatever the operator passed on the CLI —
    entries in ``domain.corpus_sources`` with no matching key are recorded
    ``absent``, never silently skipped."""
    from arail.nucleus.paths import guard_committable_output

    items: List[dict] = []
    source_rows: List[dict] = []
    cve_map: Dict[str, str] = {}

    # Pass 1: extract everything except cve mapping (git items need to
    # exist before cve labels can be applied to them).
    for kind, name in domain.corpus_sources:
        if kind not in _KNOWN_KINDS:
            raise DomainConfigError(f"unknown corpus source kind {kind!r}")
        path = provided_sources.get(name)
        if path is None:
            source_rows.append({
                "id": f"{kind}:{name}", "kind": kind, "name": name,
                "status": "absent", "license": None, "redistributable": None,
                "origin_commit": None, "items": 0,
            })
            continue

        if kind == "git":
            extracted = git_kernel.extract(Path(path), since=since, max_commits=max_commits)
            items.extend(extracted)
            origin_commit = extracted[0]["sha"] if extracted else None
            source_rows.append({
                "id": f"{kind}:{name}", "kind": kind, "name": name,
                "status": "staged", "license": git_kernel.LICENSE,
                "redistributable": git_kernel.REDISTRIBUTABLE,
                "origin_commit": origin_commit, "items": len(extracted),
            })
        elif kind == "cve":
            cve_map = cve_vulns.extract(Path(path))
            source_rows.append({
                "id": f"{kind}:{name}", "kind": kind, "name": name,
                "status": "staged", "license": cve_vulns.LICENSE,
                "redistributable": cve_vulns.REDISTRIBUTABLE,
                "origin_commit": None, "items": len(cve_map),
            })
        else:
            # Schema-reserved kind with no adapter yet (lkml/lwn/world) —
            # explicitly not staged, distinct from "absent": the operator
            # DID pass a path, but there's nothing to extract it with.
            source_rows.append({
                "id": f"{kind}:{name}", "kind": kind, "name": name,
                "status": "not_supported", "license": None, "redistributable": False,
                "origin_commit": None, "items": 0,
            })

    # Pass 2: apply CVE labels to git items by sha.
    for item in items:
        sha = item.get("sha")
        if sha and sha in cve_map:
            item["labels"]["cve"] = cve_map[sha]

    items.sort(key=lambda it: (it["date"], it["id"]))

    out_dir = _corpus_dir(domain.name, nucleus_data=nucleus_data)
    guard_committable_output(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    items_path = out_dir / "items.jsonl"
    items_bytes = b"\n".join(canonical_json(it) for it in items) + (b"\n" if items else b"")
    items_path.write_bytes(items_bytes)
    items_sha256 = hashlib.sha256(items_bytes).hexdigest()

    manifest = {
        "schema": _MANIFEST_SCHEMA,
        "domain": domain.name,
        "cutoff": domain.corpus_cutoff,
        "sources": source_rows,
        "items_sha256": items_sha256,
        "n_items": len(items),
    }
    manifest_bytes = canonical_json(manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest["sha256"] = manifest_sha256

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2))

    # Non-canonical, unhashed sidecar: the operator's own local paths for
    # same-machine reuse (evals/executable_kernel.py's patch_applies needs
    # the actual git repo at build time -- staging intentionally never
    # hashes a path, since paths aren't portable/reproducible, but a local
    # build on the SAME machine that staged the corpus can still use one
    # if it's there). Absent this file (or a missing/moved path inside
    # it), executable checks degrade to not_run -- never an error.
    local_paths = {name: str(path) for name, path in provided_sources.items()}
    (out_dir / "local_paths.json").write_text(json.dumps(local_paths, sort_keys=True, indent=2))

    return StageResult(manifest=manifest, manifest_sha256=manifest_sha256,
                       items_path=items_path, manifest_path=manifest_path,
                       n_items=len(items))


def load_staged(domain_name: str, *, nucleus_data: Optional[Path] = None) -> Optional[StageResult]:
    """Read back a previously staged snapshot, or None if nothing is
    staged yet (callers use this for the "corpus snapshot staged"
    preflight capability row and T-STAGE-2's refusal)."""
    out_dir = _corpus_dir(domain_name, nucleus_data=nucleus_data)
    manifest_path = out_dir / "manifest.json"
    items_path = out_dir / "items.jsonl"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text())
    n_items = manifest.get("n_items", 0)
    return StageResult(manifest=manifest, manifest_sha256=manifest.get("sha256", ""),
                       items_path=items_path, manifest_path=manifest_path, n_items=n_items)


def load_local_paths(domain_name: str, *, nucleus_data: Optional[Path] = None) -> Dict[str, str]:
    """Best-effort same-machine convenience read; see the sidecar note in
    stage() above. Never raises -- a missing or unreadable file just means
    no local paths are known, and callers treat that as "not available"."""
    out_dir = _corpus_dir(domain_name, nucleus_data=nucleus_data)
    local_paths_file = out_dir / "local_paths.json"
    if not local_paths_file.is_file():
        return {}
    try:
        return json.loads(local_paths_file.read_text())
    except (OSError, ValueError):
        return {}


def load_items(stage_result: StageResult) -> List[dict]:
    if not stage_result.items_path.is_file():
        return []
    out = []
    for line in stage_result.items_path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# ── CLI ──────────────────────────────────────────────────────────────

def _parse_args(argv: List[str]) -> dict:
    if not argv:
        raise DomainConfigError(
            "usage: nucleus stage <slug> --source KIND:NAME=<path> "
            "[--source ...] [--since D] [--max-commits N] [--new-cert-version]"
        )
    slug = argv[0]
    provided: Dict[str, Path] = {}
    since = None
    max_commits = None
    new_cert_version = False

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--source":
            i += 1
            if i >= len(argv):
                raise DomainConfigError("--source requires KIND:NAME=<path>")
            spec = argv[i]
            ident, _, path = spec.partition("=")
            kind, _, name = ident.partition(":")
            if not kind or not name or not path:
                raise DomainConfigError(f"--source must be KIND:NAME=<path>, got {spec!r}")
            provided[name] = Path(path)
        elif arg == "--since":
            i += 1
            since = argv[i] if i < len(argv) else None
        elif arg == "--max-commits":
            i += 1
            max_commits = int(argv[i]) if i < len(argv) else None
        elif arg == "--new-cert-version":
            new_cert_version = True
        else:
            raise DomainConfigError(f"unknown stage argument: {arg!r}")
        i += 1

    return {"slug": slug, "provided": provided, "since": since,
           "max_commits": max_commits, "new_cert_version": new_cert_version}


def run(argv: List[str]) -> int:
    import sys

    from arail.nucleus.domain import load_domain
    from arail.nucleus.models import resolve_model

    parsed = _parse_args(argv)
    domain = load_domain(parsed["slug"], model_resolver=resolve_model)
    result = stage(domain, provided_sources=parsed["provided"],
                   since=parsed["since"], max_commits=parsed["max_commits"])

    sys.stdout.write(f"staged {result.n_items} items -> {result.items_path}\n")
    sys.stdout.write(f"manifest sha256: {result.manifest_sha256}\n")
    for row in result.manifest["sources"]:
        sys.stdout.write(f"  {row['id']}: {row['status']} ({row['items']} items)\n")

    if parsed["new_cert_version"]:
        from arail.nucleus.evals.splits import CertStore

        cert = CertStore().create(domain, result)
        sys.stdout.write(f"new cert version: {cert.version}\n")

    return 0
