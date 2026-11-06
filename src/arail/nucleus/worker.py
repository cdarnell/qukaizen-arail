"""``python -m arail.nucleus.worker <phase> <build_id>`` (ARCHITECTURE.md
§4.8).

Every phase subprocess enters here first. Installs the egress guard
before doing anything else, then reads ``context.json`` (written by the
parent `build` process before phases start) and dispatches to the phase
implementation in ``build.py``. Models are loaded only from resolved
local dirs inside the phase implementations — never a repo id — which,
together with the offline env vars phases.py sets before spawning this
process, is how zero egress lines is actually achieved.

If the guard is incomplete (an HTTP library is installed that it could not
patch) the worker does NOT run the phase: it writes one ``worker: ...`` line
to stderr and exits 1. It is the one caller of ``install_guard()`` that stops
(the portal and the agent loader report and keep serving; see
docs/PRIVACY.md). The parent inherits this stderr, so the line is what a
Model Forge user reads; it must not be a traceback, and it must not be
printed twice: the guard also logs the same message, and with logging
unconfigured (as in this process) logging's last-resort handler would print
that record to stderr as a second line, so the guard's logger is given a
NullHandler for the duration of the install.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Sequence


@contextlib.contextmanager
def _guard_log_kept_off_stderr(logger_name: str):
    """While active, logging's last-resort handler does not print ``logger_name``'s records to stderr.

    The last-resort handler prints a record only when no handler is found for its logger anywhere up
    the hierarchy; a NullHandler is a handler, so the record is found to have one and nothing is printed.
    It is not ``propagate = False``: the record still reaches a handler somebody configures on the root
    logger, and the NullHandler is taken off again when the block ends, so an in-process caller of
    ``main()`` (a test) does not inherit it."""
    logger = logging.getLogger(logger_name)
    handler = logging.NullHandler()
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


def _load_context(build_id: str) -> dict:
    from arail.nucleus.paths import run_dir

    context_path = run_dir(build_id) / "context.json"
    if not context_path.is_file():
        raise RuntimeError(f"no context.json at {context_path} — build.py must write it before spawning phases")
    return json.loads(context_path.read_text())


def main(argv: Sequence[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        sys.stderr.write("usage: python -m arail.nucleus.worker <phase> <build_id>\n")
        return 2
    phase, build_id = argv

    from arail import egress

    try:
        with _guard_log_kept_off_stderr(egress.__name__):
            egress.install_guard()
    except egress.EgressGuardIncomplete as exc:
        # Still fail closed, but readable: the message names the library, says its requests are
        # not checked, and gives the fix.  This is the one place it is printed.
        sys.stderr.write(f"worker: {exc}\n")
        return 1

    try:
        context = _load_context(build_id)
        from arail.nucleus.build import run_phase_body

        output = run_phase_body(phase, context)
    except Exception as exc:  # noqa: BLE001 — the phase boundary: report, exit non-zero, no traceback leaks to the parent's stdout
        sys.stderr.write(f"worker: phase {phase} failed: {exc}\n")
        return 1

    from arail.nucleus.build import persist_phase_output

    persist_phase_output(context, phase, output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
