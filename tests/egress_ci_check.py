"""Checks on the junit XML that .github/workflows/egress-guard.yml produces.  Not a test module.

    python tests/egress_ci_check.py no-skips JUNIT FILE [FILE ...]
    python tests/egress_ci_check.py negative-control JUNIT PYTEST_EXIT_CODE

Standard library only, and Python 3.10 compatible: the workflow runs it under every Python the egress
legs use, and it must not depend on the code under test (it is run as a script, so the tests package is
not importable from it).

Why it exists (TD50).  The egress guard's tests once skipped themselves where ``httpx`` was absent and
no workflow ran them, so nothing turned red while ``httpx2`` ran past the guard.  pytest exits 0 for a
run in which every test was skipped, and for a run in which a listed file silently contributed nothing.
These two checks close that:

``no-skips``
    Exit 0 only if the junit file has no skipped, failed or errored case (an expected failure is
    recorded by junit as a skip and counts as one) and every FILE contributed at least one case.  A
    FILE contributes a case when the case's class name is the file's module (``tests/test_x.py`` is
    ``tests.test_x``) or a class or nested class in it.  The module name is matched whole, so
    ``tests.test_x`` is not satisfied by ``tests.test_x_fresh``.

``negative-control``
    The fifth leg has neither httpx nor httpx2, so the sentinel must FAIL and say why.  Exit 0 only if
    pytest exited 1, the sentinel's failure message contains UNPROVEN, and the declaration test passed.
    Anything else is red, including pytest exiting 2 or 5 (a collection problem: nothing was proven)
    and a sentinel that passed (it can pass vacuously).

Every finding is one line that says what it means and where to look.  Exit codes: 0 fine, 1 a finding,
2 misuse of the command line.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter

# The two tests in tests/test_egress_httpx.py that leg 5 runs.  tests/test_egress_ci_check.py checks
# that these names, the node ids in the workflow and the functions in that file are the same.
SENTINEL = "test_httpx_family_coverage_is_provable_in_this_environment"
DECLARATION = "test_installed_httpx_family_matches_the_ci_leg_declaration"
SENTINEL_MODULE = "tests.test_egress_httpx"
WORKFLOW = ".github/workflows/egress-guard.yml"

USAGE = (
    "usage: egress_ci_check.py no-skips JUNIT FILE [FILE ...]\n"
    "       egress_ci_check.py negative-control JUNIT PYTEST_EXIT_CODE"
)

PYTEST_EXIT_MEANINGS = {
    0: "every test passed, the sentinel included: it can pass with no library to test, so it proves nothing "
       "(or this leg is not really the 'neither' state; read its 'What this leg installed' block)",
    2: "the run was interrupted or a test module failed to collect, so nothing was proven",
    3: "pytest hit an internal error",
    4: "pytest could not resolve the node ids: one is mistyped in the workflow, or its module failed to import "
       "(a module-level import of an HTTP library or huggingface_hub in tests/test_egress_httpx.py or "
       "tests/egress_family_support.py does that in this venv, and with explicit node ids pytest exits 4 for it)",
    5: "no tests were collected (check the node ids in the workflow)",
}


class Case:
    """One testcase element: where it came from and how it ended."""

    def __init__(self, classname: str, name: str, outcome: str, message: str) -> None:
        self.classname = classname
        self.name = name
        self.outcome = outcome  # passed | failed | error | skipped
        self.message = message

    @property
    def ident(self) -> str:
        return f"{self.classname}::{self.name}" if self.classname else self.name

    @property
    def owner(self) -> str:
        """The dotted module (and class) this case belongs to.  A module that failed to import is
        recorded with an empty class name and its dotted path as the name."""
        return self.classname or self.name


class CannotRead(Exception):
    pass


def read_cases(path: str) -> list[Case]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CannotRead(f"cannot read the junit file {path}: {type(exc).__name__}: {exc}") from exc
    cases = []
    for element in root.iter("testcase"):
        children = [child for child in element if child.tag in ("failure", "error", "skipped")]
        outcome, message = "passed", ""
        for tag, label in (("error", "error"), ("failure", "failed"), ("skipped", "skipped")):
            hit = next((child for child in children if child.tag == tag), None)
            if hit is not None:
                outcome, message = label, (hit.get("message") or "").strip()
                break
        cases.append(Case(element.get("classname") or "", element.get("name") or "", outcome, message))
    return cases


def module_of(path: str) -> str:
    """tests/test_x.py -> tests.test_x"""
    path = path.replace("\\", "/")
    if path.endswith(".py"):
        path = path[: -len(".py")]
    return path.strip("/").replace("/", ".")


def belongs_to(case: Case, module: str) -> bool:
    return case.owner == module or case.owner.startswith(module + ".")


def first_line(text: str) -> str:
    """The first line of a message without its final full stop (the findings add their own)."""
    return text.splitlines()[0].strip().rstrip(".") if text.strip() else "(no message)"


def check_no_skips(path: str, files: list[str]) -> tuple[list[str], dict[str, Counter]]:
    """(findings, per-file outcome counts).  Raises CannotRead."""
    cases = read_cases(path)
    findings: list[str] = []
    for case in cases:
        if case.outcome == "skipped":
            findings.append(
                f"SKIPPED {case.ident}: {first_line(case.message)}. A skipped test proves nothing, and a skip is how "
                "TD50 stayed hidden; fix the environment or the test, do not accept the skip."
            )
        elif case.outcome in ("failed", "error"):
            findings.append(
                f"{'FAILED' if case.outcome == 'failed' else 'ERROR'} {case.ident}: {first_line(case.message)}. "
                "(pytest fails the step for this too; it is named here so one run lists every problem.)"
            )
    counts: dict[str, Counter] = {}
    for rel in files:
        module = module_of(rel)
        tally: Counter = Counter(case.outcome for case in cases if belongs_to(case, module))
        counts[rel] = tally
        if not sum(tally.values()):
            findings.append(
                f"NO TESTS from {rel}: no case in {path} belongs to module {module}. A mistyped path in "
                f"EGRESS_TESTS ({WORKFLOW}), a module that failed to collect or a renamed module makes a listed "
                "file vanish from the run while every other test still passes."
            )
    if not cases:
        findings.append(f"the junit file {path} has no test cases at all: nothing ran")
    return findings, counts


def check_negative_control(path: str, exit_code: int) -> list[str]:
    """Findings for leg 5.  Raises CannotRead."""
    findings: list[str] = []
    if exit_code != 1:
        meaning = PYTEST_EXIT_MEANINGS.get(exit_code, "an unexpected exit status")
        findings.append(
            f"pytest exited {exit_code}, but leg 5 must see exactly 1 (a test failed): {meaning}."
        )
    module = SENTINEL_MODULE.split(".")[-1]
    by_name = {case.name: case for case in read_cases(path) if case.classname.split(".")[-1] == module}

    sentinel = by_name.get(SENTINEL)
    if sentinel is None:
        findings.append(
            f"the sentinel {SENTINEL} is not in the junit file: it did not run (a collection error, or the "
            f"node id in {WORKFLOW} is wrong)."
        )
    elif sentinel.outcome == "passed":
        findings.append(
            f"the sentinel {SENTINEL} passed in a venv with neither library: it can pass vacuously, so the "
            "other legs' green proves less than they say (or this venv is not the 'neither' state)."
        )
    elif sentinel.outcome in ("skipped", "error"):
        findings.append(
            f"the sentinel {SENTINEL} ended {sentinel.outcome}, not failed: {first_line(sentinel.message)}."
        )
    elif "UNPROVEN" not in sentinel.message:
        findings.append(
            f"the sentinel {SENTINEL} failed for another reason: its message has no UNPROVEN, which is the "
            f"sentinel's own wording ({first_line(sentinel.message)})."
        )

    declaration = by_name.get(DECLARATION)
    if declaration is None:
        findings.append(
            f"the declaration test {DECLARATION} is not in the junit file: it did not run."
        )
    elif declaration.outcome != "passed":
        findings.append(
            f"the declaration test {DECLARATION} did not pass ({declaration.outcome}: {first_line(declaration.message)}). "
            "This leg declares ARAIL_TEST_EXPECT_HTTPX_FAMILY=none, so the venv must really have neither "
            "library; read the leg's 'What this leg installed' block."
        )
    return findings


def report(title: str, findings: list[str]) -> int:
    for line in findings:
        print(line)
    if findings:
        print(f"egress ci check ({title}): {len(findings)} finding(s); this leg is red.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    def usage(why: str) -> int:
        print(f"egress_ci_check.py: {why}\n{USAGE}", file=sys.stderr)
        return 2

    if not argv:
        return usage("no command given")
    command, args = argv[0], argv[1:]
    try:
        if command == "no-skips":
            if len(args) < 2:
                return usage("no-skips needs a junit file and at least one test file")
            findings, counts = check_no_skips(args[0], args[1:])
            for rel, tally in counts.items():
                done = ", ".join(f"{n} {label}" for label, n in sorted(tally.items())) or "nothing"
                print(f"  {rel}: {done}")
            code = report("no-skips", findings)
            if not code:
                total = sum(sum(tally.values()) for tally in counts.values())
                print(f"egress ci check (no-skips): ok - {len(counts)} files, {total} cases, none skipped, failed or missing.")
            return code
        if command == "negative-control":
            if len(args) != 2:
                return usage("negative-control needs a junit file and pytest's exit code")
            try:
                exit_code = int(args[1])
            except ValueError:
                return usage(f"pytest's exit code must be an integer, got {args[1]!r}")
            code = report("negative-control", check_negative_control(args[0], exit_code))
            if not code:
                print("egress ci check (negative-control): ok - the sentinel failed with UNPROVEN, as it must "
                      "with neither library, and the declaration passed.")
            return code
    except CannotRead as exc:
        print(str(exc))
        return 1
    return usage(f"unknown command {command!r}")


if __name__ == "__main__":
    sys.exit(main())
