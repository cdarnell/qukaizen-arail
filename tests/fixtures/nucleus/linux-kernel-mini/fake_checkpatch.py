#!/usr/bin/env python3
"""A stand-in for checkpatch.pl's CLI contract, used by
evals/executable_kernel.py's tests (commit 15) instead of vendoring the
real GPL-2.0 script into this MIT repo.

Contract mirrored: ``fake_checkpatch.py --no-tree --terse -`` reads a
unified diff on stdin. A line added ending in trailing whitespace is
flagged (one deterministic, trivially-triggerable "issue" so both the
clean and the dirty path are exercised); exit 1 with a WARNING line on
stdin's content, exit 0 with no output otherwise.
"""

from __future__ import annotations

import sys


def main(argv: list) -> int:
    if "--no-tree" not in argv or "--terse" not in argv:
        sys.stderr.write("fake_checkpatch.py: expected --no-tree --terse -\n")
        return 2

    patch = sys.stdin.read()
    issues = []
    for lineno, line in enumerate(patch.splitlines(), start=1):
        if line.startswith("+") and not line.startswith("+++") and line.rstrip("\n") != line.rstrip():
            issues.append(f"WARNING: trailing whitespace at line {lineno}")
        if line.startswith("+") and not line.startswith("+++") and "\ttrailing_ws" in line:
            issues.append(f"WARNING: trailing whitespace marker at line {lineno}")

    if issues:
        sys.stdout.write("\n".join(issues) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
