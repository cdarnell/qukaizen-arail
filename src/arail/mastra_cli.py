"""``./arailctl mastra <install|start|stop|open|run-once|status>`` (maximus, opt-in)."""
from __future__ import annotations

import json
import subprocess
import sys

from arail import mastra_bridge as mb


def main(argv: list) -> int:
    try:
        return _main(argv)
    except OSError as exc:
        print(f"Could not complete: {exc.strerror or 'file system error'}"
              f"{' (' + exc.filename + ')' if exc.filename else ''}. Check that lab/data is writable.",
              file=sys.stderr)
        return 1


def _main(argv: list) -> int:
    op = argv[0] if argv else "status"
    if op == "install":
        return mb.install()
    if op == "start":
        r = mb.start_studio()
        print(json.dumps(r))
        return 0 if r.get("ok") else 1
    if op == "stop":
        print(json.dumps(mb.stop_studio()))
        return 0
    if op == "status":
        print(json.dumps(mb.resolve_page_state()))
        return 0
    if op == "open":
        exe = mb.find_browser()
        if not exe:
            print("No Chrome/Chromium found. Install one, or open nothing: a normal browser "
                  "would make ~16 non-loopback requests.", file=sys.stderr)
            return 2
        mb.browser_profile_dir().mkdir(parents=True, exist_ok=True)
        subprocess.Popen([exe, *mb.browser_args()], shell=False, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0
    if op == "run-once":
        if len(argv) < 2:
            print('usage: ./arailctl mastra run-once "<goal>" [experiment_id]', file=sys.stderr)
            return 2
        r = mb.run_probe(argv[1], argv[2] if len(argv) > 2 else None)
        print(json.dumps(r))
        return 1 if "error" in r else 0
    print("usage: ./arailctl mastra install|start|stop|open|run-once|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
