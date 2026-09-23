"""``python -m arail.nucleus.worker <phase> <build_id>`` (ARCHITECTURE.md
§4.8).

Every phase subprocess enters here first. Installs the egress guard
before doing anything else, then reads ``context.json`` (written by the
parent `build` process before phases start) and dispatches to the phase
implementation in ``build.py``. Models are loaded only from resolved
local dirs inside the phase implementations — never a repo id — which,
together with the offline env vars phases.py sets before spawning this
process, is how zero egress lines is actually achieved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence


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

    egress.install_guard()

    try:
        context = _load_context(build_id)
        from arail.nucleus.build import run_phase_body

        output = run_phase_body(phase, context)
    except Exception as exc:  # noqa: BLE001 — the phase boundary: report, exit non-zero, no traceback leaks to the parent's stdout
        sys.stderr.write(f"worker: phase {phase} failed: {exc}\n")
        return 1

    from arail.nucleus.paths import run_dir

    out_dir = run_dir(build_id) / "phase_output"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (out_dir / f"{phase}.json").write_text(json.dumps(output or {}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
