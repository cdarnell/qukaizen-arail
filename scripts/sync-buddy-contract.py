#!/usr/bin/env python3
"""Vendor Buddy's host contract into ARAIL with a hash stamp.

qukaizen-buddy owns ``buddy/src/buddy/contracts/`` (``buddy.host.v1.json`` and
``conformance/*.json``). ARAIL keeps a byte-identical copy in
``src/arail/buddy_host/contract/`` and a ``PROVENANCE.json`` stamp beside it,
the pattern of qukaizen-site's ``gate.generated.ts`` (INTEGRATION_AUDIT.md §4).

    python scripts/sync-buddy-contract.py --from "$QKZ_BUDDY_PATH"   # copy + stamp
    python scripts/sync-buddy-contract.py --check                    # verify

``--check`` is sibling-free: it re-hashes the vendored files against the stamp
(and the stamp's own ``tree_digest``). If a qukaizen-buddy checkout exists
(``$QKZ_BUDDY_PATH``, default ``../qukaizen-buddy``) it also compares bytes with
the source and reports a moved source commit.

Exit codes: 0 ok · 1 vendored copy does not match its stamp · 2 usage, missing
or dirty source · 3 vendored copy differs from the sibling's bytes.

``tree_digest`` is sha256 over the lines ``"<path> <sha256 hex>\\n"`` of every
stamped file, sorted by path.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_VENDOR = REPO / "src" / "arail" / "buddy_host" / "contract"
SOURCE_REPO = "cdarnell/qukaizen-buddy"
SOURCE_DIR = "buddy/src/buddy/contracts"
CONTRACT_NAME = "buddy.host.v1.json"
STAMP_NAME = "PROVENANCE.json"
SYNC_CMD = 'python scripts/sync-buddy-contract.py --from "$QKZ_BUDDY_PATH"'
CHECK_CMD = "python scripts/sync-buddy-contract.py --check"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(root: Path) -> dict[str, bytes]:
    """The files that make up the contract: the contract itself and conformance/*.json."""
    files: dict[str, bytes] = {}
    contract = root / CONTRACT_NAME
    if contract.is_file():
        files[CONTRACT_NAME] = contract.read_bytes()
    conf = root / "conformance"
    if conf.is_dir():
        for p in sorted(conf.glob("*.json")):
            files[f"conformance/{p.name}"] = p.read_bytes()
    return files


def tree_digest(hashes: dict[str, str]) -> str:
    lines = "".join(f"{path} {hexd}\n" for path, hexd in sorted(hashes.items()))
    return "sha256:" + sha256_hex(lines.encode())


def build_stamp(files: dict[str, bytes], commit: str, today: str) -> dict:
    hashes = {p: sha256_hex(b) for p, b in sorted(files.items())}
    return {
        "schema": "arail.vendor-stamp/v1",
        "source_repo": SOURCE_REPO,
        "source_commit": commit,
        "source_dir": SOURCE_DIR,
        "files": {p: "sha256:" + h for p, h in hashes.items()},
        "tree_digest": tree_digest(hashes),
        "synced_on": today,
        "sync": SYNC_CMD,
        "check": CHECK_CMD,
    }


def _git(source: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git {' '.join(args)} failed")
    return out.stdout.strip()


def _sibling_path(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("QKZ_BUDDY_PATH") or str(REPO.parent / "qukaizen-buddy")
    return Path(raw).expanduser()


# --------------------------------------------------------------------------- sync
def cmd_sync(source_arg: str, vendor: Path, allow_dirty: bool) -> int:
    source = _sibling_path(source_arg)
    src_dir = source / SOURCE_DIR
    if not (src_dir / CONTRACT_NAME).is_file():
        print(f"error: {src_dir / CONTRACT_NAME} not found; pass --from <qukaizen-buddy checkout>", file=sys.stderr)
        return 2
    try:
        commit = _git(source, "rev-parse", "HEAD")
        dirty = _git(source, "status", "--porcelain", "--", SOURCE_DIR)
    except (RuntimeError, OSError) as exc:
        print(f"error: {source} is not a usable git checkout: {exc}", file=sys.stderr)
        return 2
    if dirty and not allow_dirty:
        print("error: uncommitted changes under " + SOURCE_DIR + " in " + str(source) +
              "; commit them first (the stamp names a commit) or pass --allow-dirty:\n" + dirty, file=sys.stderr)
        return 2
    files = collect(src_dir)
    vendor.mkdir(parents=True, exist_ok=True)
    (vendor / "conformance").mkdir(parents=True, exist_ok=True)
    for stale in (vendor / "conformance").glob("*.json"):
        if f"conformance/{stale.name}" not in files:
            stale.unlink()
    for rel, data in files.items():
        (vendor / rel).write_bytes(data)
    stamp = build_stamp(files, commit, dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    (vendor / STAMP_NAME).write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print(f"synced {len(files)} files from {source} @ {commit[:12]} -> {vendor}")
    if dirty:
        print("warning: source had uncommitted changes (--allow-dirty); the stamped commit does not hold these bytes")
    return 0


# -------------------------------------------------------------------------- check
def check_stamp(vendor: Path) -> list[str]:
    """Problems with the vendored copy against its own stamp. Empty means consistent."""
    stamp_path = vendor / STAMP_NAME
    if not stamp_path.is_file():
        return [f"{STAMP_NAME} is missing from {vendor}"]
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{STAMP_NAME} is not valid JSON: {exc}"]
    problems: list[str] = []
    if stamp.get("schema") != "arail.vendor-stamp/v1":
        problems.append(f"{STAMP_NAME}: unknown schema {stamp.get('schema')!r}")
    stamped = stamp.get("files") or {}
    actual = collect(vendor)
    for rel in sorted(set(stamped) | set(actual)):
        if rel not in actual:
            problems.append(f"{rel}: stamped but missing on disk")
        elif rel not in stamped:
            problems.append(f"{rel}: on disk but not in the stamp")
        elif stamped[rel] != "sha256:" + sha256_hex(actual[rel]):
            problems.append(f"{rel}: bytes differ from the stamp")
    expected_digest = tree_digest({p: v.removeprefix("sha256:") for p, v in stamped.items()})
    if stamp.get("tree_digest") != expected_digest:
        problems.append("tree_digest does not match the stamped file hashes")
    return problems


def cmd_check(vendor: Path, source_arg: str | None) -> int:
    problems = check_stamp(vendor)
    if problems:
        print("FAIL: vendored buddy.host/v1 contract does not match its stamp:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        print("  re-vendor with: " + SYNC_CMD, file=sys.stderr)
        return 1
    stamp = json.loads((vendor / STAMP_NAME).read_text(encoding="utf-8"))
    sibling = _sibling_path(source_arg)
    src_dir = sibling / SOURCE_DIR
    if not (src_dir / CONTRACT_NAME).is_file():
        print(f"ok: {len(stamp['files'])} vendored files match the stamp (sibling-free; no qukaizen-buddy at {sibling})")
        return 0
    source_files = collect(src_dir)
    vendored = collect(vendor)
    drift = [
        f"{rel}: " + ("only in the source" if rel not in vendored else "only vendored" if rel not in source_files else "bytes differ")
        for rel in sorted(set(source_files) | set(vendored))
        if source_files.get(rel) != vendored.get(rel)
    ]
    if drift:
        print(f"DRIFT: the vendored contract differs from {sibling}:", file=sys.stderr)
        for d in drift:
            print("  " + d, file=sys.stderr)
        print("  re-vendor with: " + SYNC_CMD, file=sys.stderr)
        return 3
    note = ""
    try:
        head = _git(sibling, "rev-parse", "HEAD")
        if head != stamp.get("source_commit"):
            note = (f"; note: the source has moved from {str(stamp.get('source_commit'))[:12]} to {head[:12]} "
                    "but the contract bytes are unchanged")
    except (RuntimeError, OSError):
        note = "; note: the sibling is not a git checkout, source commit not compared"
    print(f"ok: {len(stamp['files'])} vendored files match the stamp and the sibling's bytes{note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", metavar="PATH", help="qukaizen-buddy checkout to vendor from")
    ap.add_argument("--check", action="store_true", help="verify the vendored copy (sibling-free; also compares bytes if the sibling exists)")
    ap.add_argument("--allow-dirty", action="store_true", help="vendor from a source with uncommitted changes (the stamp will not hold these bytes)")
    ap.add_argument("--vendor-dir", metavar="PATH", default=str(DEFAULT_VENDOR), help="where the vendored copy lives (default: %(default)s)")
    args = ap.parse_args(argv)
    vendor = Path(args.vendor_dir)
    if args.check:
        return cmd_check(vendor, args.source)
    if args.source:
        return cmd_sync(args.source, vendor, args.allow_dirty)
    ap.print_usage(sys.stderr)
    print("error: pass --from PATH to sync or --check to verify", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
