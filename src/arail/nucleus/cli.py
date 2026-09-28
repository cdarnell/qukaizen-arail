"""``arailctl nucleus <verb>`` dispatch — arail.nucleus's only CLI entry point.

Exit codes (ARCHITECTURE.md §4.12):
    0  ok
    1  internal error
    2  usage / invalid config
    3  refused by policy (tier, airgap, preflight, contamination, lock, capability)

Errors are one plain-language paragraph; tracebacks only appear with
``--debug`` (T-CLI-3). Each verb implementation lives in its own module
(``plan.py``, ``build.py``, ...) as they land in later commits — this
module only does dispatch, tier gating, and the exit/traceback contract.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional, Sequence

from arail.nucleus.errors import CapabilityMissing, NucleusError, RefusedByPolicy

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3

MAXIMUS_REFUSAL = (
    "needs the maximus deep runtime — `./arailctl tier maximus`, "
    "then `./arailctl deep install`"
)

PUBLISH_REFUSAL = "registry publication lands in nucleus-sprint-3"

# verb -> minimum tier ("minimalist" verbs also run on maximus).
_VERB_TIERS: dict[str, str] = {
    "plan": "minimalist",
    "stage": "maximus",
    "build": "maximus",
    "spike": "maximus",
    "eval": "maximus",
    "certify": "maximus",
    "verify": "minimalist",
    "status": "minimalist",
    "list": "minimalist",
    "publish": "minimalist",  # tier check is moot — publish always refuses below
}

_USAGE = """usage: arailctl nucleus <verb> [args]

verbs:
  plan "<intent>" --name <slug> | plan <slug>   preflight-only domain plan
  stage <slug> --source KIND:NAME [...]          stage a local corpus snapshot
  build <slug> --profile local [...]             run the full build pipeline
  spike <slug> [--windows N] [--cert-sample N]   Gate B spike harness
  eval <shard>@<ver>                             re-run eval on a built shard
  certify <slug|build_id> [--publish-row]        contamination gate -> card -> seal
  verify <shard>@<ver>|<dir>                     verify a card's seal + hashes
  status [ID]                                    build/run status
  list                                            list certified shards
  publish                                         (not available this sprint)

Flags:
  --debug   print a full traceback on internal errors instead of one line
"""


def _print_usage(stream) -> None:
    stream.write(_USAGE)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    debug = "--debug" in args
    if debug:
        args = [a for a in args if a != "--debug"]

    if not args or args[0] in ("-h", "--help"):
        _print_usage(sys.stdout)
        return EXIT_OK if args and args[0] in ("-h", "--help") else EXIT_USAGE

    verb, rest = args[0], args[1:]

    if verb not in _VERB_TIERS:
        sys.stderr.write(f"arailctl nucleus: unknown verb {verb!r}\n\n")
        _print_usage(sys.stderr)
        return EXIT_USAGE

    if verb == "publish":
        sys.stderr.write(f"{PUBLISH_REFUSAL}\n")
        return EXIT_REFUSED

    if _VERB_TIERS[verb] == "maximus":
        from arail.tier import is_maximus

        if not is_maximus():
            sys.stderr.write(f"{MAXIMUS_REFUSAL}\n")
            return EXIT_REFUSED

    try:
        return _dispatch(verb, rest)
    except (RefusedByPolicy, CapabilityMissing) as exc:
        sys.stderr.write(f"{exc}\n")
        if debug:
            raise
        return EXIT_REFUSED
    except NucleusError as exc:
        sys.stderr.write(f"{exc}\n")
        if debug:
            raise
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 — the CLI boundary: never leak a traceback
        sys.stderr.write(f"internal error: {exc}\n")
        if debug:
            raise
        return EXIT_INTERNAL


# verb -> callable(argv) -> exit code. Populated as each verb lands
# (commit 5 adds "plan", commit 18 adds "build"/"eval"/"status", commit 20
# adds "verify", commit 21 adds "certify", commit 24 adds "spike"). Verbs
# not yet in this map fall through to the "not implemented yet" stub.
_IMPLEMENTED: dict[str, Callable[[list], int]] = {}


def _register_verbs() -> None:
    """Deferred import so `cli.py` stays importable even before every verb
    module exists yet (build order, ARCHITECTURE.md §10)."""
    from arail.nucleus import build as _build
    from arail.nucleus import certify as _certify
    from arail.nucleus import plan as _plan
    from arail.nucleus import spike as _spike
    from arail.nucleus.cards import seal as _seal
    from arail.nucleus.corpus import stage as _stage

    _IMPLEMENTED["plan"] = _plan.run
    _IMPLEMENTED["stage"] = _stage.run
    _IMPLEMENTED["build"] = _build.run
    _IMPLEMENTED["status"] = _build.status
    _IMPLEMENTED["verify"] = _seal.run
    _IMPLEMENTED["certify"] = _certify.run
    _IMPLEMENTED["spike"] = _spike.run


_register_verbs()


def _dispatch(verb: str, rest: list) -> int:
    if verb in _IMPLEMENTED:
        return _IMPLEMENTED[verb](rest)
    sys.stderr.write(f"arailctl nucleus {verb}: not implemented yet\n")
    return EXIT_INTERNAL
