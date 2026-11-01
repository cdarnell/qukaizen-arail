"""``./arailctl db`` — plan, apply, doctor, optimize, drift.

Every command prints full absolute paths, because the operator running this
may have several World instances on disk and "the database" is ambiguous
without one.

Exit codes:
    0  success / no drift
    1  a command failed
    3  doctor found errors, or drift was detected
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from arail.dbspec import atlas, migrate as migrate_mod, reconcile
from arail.dbspec.codegen import GENERATED_DIR, generate_all
from arail.dbspec.db import applied_version, connect, database_path, record_version
from arail.dbspec.doctor import run_doctor
from arail.dbspec.spec import Spec, SpecError, load_spec

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PROBLEM = 3

MIGRATIONS_DIRNAME = "migrations"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _roots(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve (data_dir, pkb_root), honoring explicit flags over env."""
    args.data_dir = getattr(args, "data_dir", None)
    args.pkb_root = getattr(args, "pkb_root", None)
    if args.data_dir:
        data_dir = Path(args.data_dir).resolve()
    else:
        from arail import config
        data_dir = Path(config.DATA_DIR).resolve()
    if args.pkb_root:
        pkb_root = Path(args.pkb_root).resolve()
    else:
        from arail import config
        pkb_root = Path(config.PKB_ROOT).resolve()
    return data_dir, pkb_root


def _load(args: argparse.Namespace) -> Spec:
    return load_spec(Path(getattr(args, "spec_dir", "spec")).resolve())


def _header(spec: Spec, data_dir: Path, pkb_root: Path) -> str:
    return (
        f"spec      {spec.spec_dir.resolve()}  (sha256 {spec.sha256[:12]})\n"
        f"database  {database_path(data_dir)}\n"
        f"data dir  {data_dir}\n"
        f"pkb root  {pkb_root}"
    )


def _generated_is_stale(spec: Spec) -> list[str]:
    """Committed generated files that no longer match the spec."""
    import tempfile

    stale: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        generate_all(spec, out_dir=tmp)
        for name in ("models_registry.py", "world_resolver.py"):
            fresh = Path(tmp) / name
            committed = GENERATED_DIR / name
            if not committed.exists():
                stale.append(str(committed.resolve()))
            elif fresh.read_text() != committed.read_text():
                stale.append(str(committed.resolve()))
    return stale


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_plan(args: argparse.Namespace) -> int:
    spec = _load(args)
    data_dir, pkb_root = _roots(args)
    print(_header(spec, data_dir, pkb_root))
    print()

    sql = atlas.schema_diff(database_path(data_dir), spec.schema_sql_path)
    print("relational (SQLite via Atlas)")
    if not sql:
        print("  in sync — no statements")
    else:
        for statement in sql:
            print(f"  {statement}")
    print()

    plan = reconcile.plan(spec, str(data_dir), str(pkb_root))
    print(plan.render())
    print()

    stale = _generated_is_stale(spec)
    print("generated code")
    if not stale:
        print("  in sync")
    else:
        for path in stale:
            print(f"  STALE  {path}")
    print()
    print("no changes were made. Run './arailctl db apply' to execute.")
    return EXIT_OK


def cmd_apply(args: argparse.Namespace) -> int:
    spec = _load(args)
    data_dir, pkb_root = _roots(args)
    print(_header(spec, data_dir, pkb_root))
    print()

    # 1. Versioned migration + lint gate. Lint failures block.
    migrations_dir = spec.spec_dir / "schema" / MIGRATIONS_DIRNAME
    migration = atlas.migrate_diff(args.migration_name, spec.schema_sql_path,
                                   migrations_dir)
    if migration is not None:
        print(f"generated migration {migration.resolve()}")
    else:
        print("no new migration needed")
    lint = atlas.lint_migrations(migrations_dir)
    print(lint.render())
    if not lint.ok:
        print("\nlint failed — nothing was applied.", file=sys.stderr)
        return EXIT_FAILED
    print()

    # 2. Relational schema.
    report = atlas.schema_apply(database_path(data_dir), spec.schema_sql_path)
    print("relational (SQLite via Atlas)")
    print(f"  applied to {database_path(data_dir)}")
    if report and args.verbose:
        print("\n".join(f"  {line}" for line in report.splitlines()))
    print()

    # 3. Vector stores.
    plan = reconcile.plan(spec, str(data_dir), str(pkb_root))
    if plan.is_empty:
        print("vector stores: in sync")
    else:
        applied = reconcile.apply(plan, allow_destructive=args.allow_destructive)
        print("vector stores")
        for line in applied:
            print(f"  {line}")
    print()

    # 4. Generated code.
    written = generate_all(spec)
    print("generated code")
    for path in written:
        print(f"  {path.resolve()}")
    print()

    # 5. Record the applied spec version.
    conn = connect(data_dir)
    try:
        record_version(conn, spec.version, spec.sha256, _now())
    finally:
        conn.close()
    print(f"recorded spec version {spec.version} (sha256 {spec.sha256[:12]})")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    spec = _load(args)
    data_dir, pkb_root = _roots(args)
    print(_header(spec, data_dir, pkb_root))
    print()

    conn = connect(data_dir)
    try:
        version = applied_version(conn)
        if version is None:
            print("  schema has never been applied — run './arailctl db apply'")
        elif version[1] != spec.sha256:
            print(f"  applied spec sha256 {version[1][:12]} differs from the "
                  f"current spec {spec.sha256[:12]} — run './arailctl db apply'")
        report = run_doctor(spec, conn, str(data_dir), str(pkb_root),
                            user_id=args.user)
    finally:
        conn.close()

    print(report.render())

    from arail.dbspec.embed import probe
    ok, message = probe()
    print()
    print(f"embedding: {'OK' if ok else 'UNAVAILABLE'} — {message}")

    return EXIT_OK if report.ok else EXIT_PROBLEM


def cmd_optimize(args: argparse.Namespace) -> int:
    spec = _load(args)
    data_dir, pkb_root = _roots(args)
    print(_header(spec, data_dir, pkb_root))
    print()
    for line in reconcile.optimize(spec, str(data_dir), str(pkb_root)):
        print(f"  {line}")
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace) -> int:
    """One-shot ARAIL 1.x -> 2.0 data migration. Dry-run by default."""
    spec = _load(args)
    data_dir, pkb_root = _roots(args)
    lab_root = Path(args.lab_root).resolve() if args.lab_root else \
        Path(os.getenv("LAB_ROOT", "lab")).resolve()
    print(_header(spec, data_dir, pkb_root))
    print(f"1.x lab root  {lab_root}")
    print()

    plan = migrate_mod.discover(str(lab_root), spec=spec)
    print(plan.render())
    print()

    lines = migrate_mod.migrate(
        plan, target_data_dir=str(data_dir), target_pkb_root=str(pkb_root),
        user_id=args.user, apply=args.apply, spec=spec)
    for line in lines:
        print(line)
    return EXIT_OK


def cmd_drift(args: argparse.Namespace) -> int:
    """CI gate: non-zero exit if actual != spec."""
    spec = _load(args)
    data_dir, pkb_root = _roots(args)
    problems: list[str] = []

    sql = atlas.schema_diff(database_path(data_dir), spec.schema_sql_path)
    if sql:
        problems.append(
            f"relational schema is {len(sql)} statement(s) behind "
            f"{spec.schema_sql_path.resolve()}")

    plan = reconcile.plan(spec, str(data_dir), str(pkb_root))
    if not plan.is_empty:
        problems.append(
            f"vector stores have {len(plan.changes)} pending change(s)")

    for path in _generated_is_stale(spec):
        problems.append(f"generated file is stale: {path}")

    if not problems:
        print(f"no drift — actual matches {spec.spec_dir.resolve()} "
              f"(sha256 {spec.sha256[:12]})")
        return EXIT_OK

    print("DRIFT DETECTED", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print("\nRun './arailctl db plan' for detail, './arailctl db apply' to fix.",
          file=sys.stderr)
    return EXIT_PROBLEM


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Roots live on a parent parser so they are accepted both before and
    # after the subcommand — `db --data-dir X plan` and `db plan --data-dir X`
    # both work, which matters because arailctl forwards the subcommand first.
    # default=SUPPRESS is load-bearing. When an option is defined on BOTH the
    # main parser and a subparser, argparse applies the subparser's default
    # AFTER parsing, clobbering a value given before the subcommand — so
    # `db --data-dir X drift` would silently fall back to the ambient lab and
    # operate on the wrong database. SUPPRESS makes the subparser leave the
    # attribute alone when the flag is absent.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--spec-dir", default=argparse.SUPPRESS,
                        help="spec tree root (default: spec)")
    common.add_argument("--data-dir", default=argparse.SUPPRESS,
                        help="lab data dir (default: ARAIL_DATA_DIR)")
    common.add_argument("--pkb-root", default=argparse.SUPPRESS,
                        help="PKB root (default: LAB_PKB)")

    parser = argparse.ArgumentParser(
        prog="arailctl db", parents=[common],
        description="Declarative persistence: spec tree -> SQLite + LanceDB.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", parents=[common], help="diff spec vs actual; no writes")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", parents=[common], help="apply schema + reconcile + regenerate")
    p.add_argument("--allow-destructive", action="store_true",
                   help="permit dimension/metric/index-type rebuilds")
    p.add_argument("--migration-name", default="spec",
                   help="name for the generated migration")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("doctor", parents=[common], help="integrity checks, reported per user")
    p.add_argument("--user", default=None, help="attribute findings to a user")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("optimize", parents=[common], help="compact Lance tables and prune versions")
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("drift", parents=[common], help="CI gate; non-zero exit if actual != spec")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("migrate", parents=[common],
                       help="one-shot 1.x -> 2.0 data migration; dry-run by default")
    p.add_argument("--lab-root", default=None,
                   help="1.x lab root to migrate from (default: LAB_ROOT env or 'lab')")
    p.add_argument("--user", default="local",
                   help="user id to attribute migrated worlds to (default: local)")
    p.add_argument("--apply", action="store_true",
                   help="execute the migration (default: dry-run)")
    p.set_defaults(func=cmd_migrate)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except SpecError as exc:
        print(f"spec error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except (atlas.AtlasError, reconcile.ReconcileError,
            migrate_mod.MigrationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
