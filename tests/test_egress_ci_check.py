"""The checker behind .github/workflows/egress-guard.yml can fail (TD50).

The workflow's whole point is that the guard's tests cannot pass vacuously.  Its two failure
conditions that pytest does not report on its own (a skipped test, a listed file that contributed no
test) and its fifth leg (a venv with neither library, where the sentinel must FAIL) are decided by
tests/egress_ci_check.py.  A checker that always says "fine" would turn every leg green while proving
nothing, which is the failure this sprint exists to remove, so these tests feed it synthetic junit XML
and show that it says "not fine" for each thing it is there to catch (CI-1 to CI-4), and then tie the
workflow, the checker and the sentinel tests together (CI-5, CI-6): rename the sentinel, or mistype a
node id in the workflow, and the negative-control leg would pass for the wrong reason.

No network, no pytest-in-pytest: the XML is written by hand in the shape pytest's junit writer uses.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest
import yaml

from tests import egress_ci_check as check

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tests" / "egress_ci_check.py"
WORKFLOW = ROOT / ".github" / "workflows" / "egress-guard.yml"

FILES = ["tests/test_httpx_family.py", "tests/test_egress_httpx.py", "tests/test_egress_httpx_fresh.py"]
SENTINEL = "test_httpx_family_coverage_is_provable_in_this_environment"
DECLARATION = "test_installed_httpx_family_matches_the_ci_leg_declaration"
UNPROVEN = (
    "AssertionError: egress coverage of the httpx family is UNPROVEN in this environment: neither "
    "httpx2 nor httpx is importable, so no test here exercised the guard on them."
)


def junit(cases) -> str:
    """pytest's junit shape.  ``cases`` is (classname, name, outcome[, message[, text]]); outcome is
    passed, failed, error, skipped or xfail."""
    out = ['<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite name="pytest">']
    for case in cases:
        classname, name, outcome, *rest = case
        message = rest[0] if rest else ""
        text = rest[1] if len(rest) > 1 else ""
        head = f"<testcase classname={quoteattr(classname)} name={quoteattr(name)} time=\"0.001\""
        if outcome == "passed":
            out.append(head + " />")
            continue
        tag, extra = {
            "failed": ("failure", ""),
            "error": ("error", ""),
            "skipped": ("skipped", ' type="pytest.skip"'),
            "xfail": ("skipped", ' type="pytest.xfail"'),
        }[outcome]
        out.append(f"{head}><{tag}{extra} message={quoteattr(message)}>{text}</{tag}></testcase>")
    out.append("</testsuite></testsuites>")
    return "".join(out)


def write(tmp_path, cases, name="junit.xml") -> Path:
    path = tmp_path / name
    path.write_text(junit(cases), encoding="utf-8")
    return path


def clean_cases(files=FILES):
    """Two passing tests from every file; the second of each in a class, as the happy-setup file is."""
    cases = []
    for rel in files:
        module = rel[: -len(".py")].replace("/", ".")
        cases.append((module, "test_one", "passed"))
        cases.append((module + ".TestSomething", "test_two", "passed"))
    return cases


def run_no_skips(tmp_path, cases, files=FILES, capsys=None):
    junit_path = write(tmp_path, cases)
    code = check.main(["no-skips", str(junit_path), *files])
    return code, capsys.readouterr().out if capsys else ""


# --- CI-1: a clean run passes ------------------------------------------------------------------

def test_a_clean_run_passes_no_skips(tmp_path, capsys):
    """CI-1."""
    code, out = run_no_skips(tmp_path, clean_cases(), capsys=capsys)
    assert code == 0, out
    assert "ok" in out
    for rel in FILES:  # the per-file counts reach the CI log
        assert rel in out


def test_the_command_line_the_workflow_uses_works_and_exits_zero(tmp_path):
    """CI-1, through the interface the workflow calls: `python tests/egress_ci_check.py no-skips JUNIT FILE...`
    run as a script, so it may not rely on the tests package being importable."""
    junit_path = write(tmp_path, clean_cases())
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "no-skips", str(junit_path), *FILES],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- CI-2: a skipped, failed or errored case fails it -------------------------------------------

def test_a_skipped_case_fails_no_skips(tmp_path, capsys):
    """CI-2.  A skip is how TD50 stayed hidden, so even a skip with a good reason is red."""
    cases = clean_cases() + [("tests.test_egress_httpx", "test_x[httpx]", "skipped", "httpx is not installed")]
    code, out = run_no_skips(tmp_path, cases, capsys=capsys)
    assert code == 1
    assert "SKIPPED" in out and "test_x[httpx]" in out and "httpx is not installed" in out
    assert "TD50" in out  # it says why a skip is not acceptable


def test_an_expected_failure_counts_as_a_skip(tmp_path, capsys):
    """CI-2.  junit records an xfail as a skip; a test that is allowed to fail is not proof either."""
    cases = clean_cases() + [("tests.test_egress_httpx", "test_y", "xfail", "known bug")]
    code, out = run_no_skips(tmp_path, cases, capsys=capsys)
    assert code == 1 and "SKIPPED" in out


@pytest.mark.parametrize("outcome,word", [("failed", "FAILED"), ("error", "ERROR")])
def test_a_failed_or_errored_case_fails_no_skips(tmp_path, capsys, outcome, word):
    """CI-2.  pytest already exits non-zero for these; the checker names them too, so that one run lists
    everything wrong."""
    cases = clean_cases() + [("tests.test_egress_httpx", "test_z", outcome, "AssertionError: boom")]
    code, out = run_no_skips(tmp_path, cases, capsys=capsys)
    assert code == 1
    assert word in out and "test_z" in out and "AssertionError: boom" in out


def test_one_run_lists_every_problem(tmp_path, capsys):
    cases = clean_cases(FILES[:2]) + [
        ("tests.test_egress_httpx", "test_s", "skipped", "r1"),
        ("tests.test_egress_httpx", "test_f", "failed", "m2"),
    ]
    code, out = run_no_skips(tmp_path, cases, capsys=capsys)
    assert code == 1
    assert "SKIPPED" in out and "FAILED" in out and FILES[2] in out and "NO TESTS" in out


# --- CI-3: a listed file that contributed no test fails it --------------------------------------

def test_a_listed_file_with_no_cases_fails_no_skips(tmp_path, capsys):
    """CI-3.  A mistyped path in the workflow, a collection error, a renamed module: the file vanishes
    from the run and every remaining test still passes."""
    cases = clean_cases(FILES[:2])  # nothing from FILES[2]
    code, out = run_no_skips(tmp_path, cases, capsys=capsys)
    assert code == 1
    assert "NO TESTS" in out and FILES[2] in out


def test_a_module_whose_name_merely_starts_like_the_listed_file_does_not_count(tmp_path, capsys):
    """CI-3.  tests/test_egress_httpx.py must not be satisfied by tests/test_egress_httpx_fresh.py (the
    startswith accident, in a new place)."""
    cases = [("tests.test_egress_httpx_fresh", "test_a", "passed"), ("tests.test_httpx_family", "test_b", "passed")]
    code, out = run_no_skips(tmp_path, cases, files=["tests/test_egress_httpx.py", "tests/test_httpx_family.py"], capsys=capsys)
    assert code == 1
    assert "NO TESTS from tests/test_egress_httpx.py" in out
    assert "NO TESTS from tests/test_httpx_family.py" not in out


def test_a_collection_error_is_an_error(tmp_path, capsys):
    """CI-3.  pytest records a module that failed to import as an error with an empty classname and the
    module's dotted path as the name."""
    cases = clean_cases(FILES[:2]) + [("", "tests.test_egress_httpx_fresh", "error", "collection failure")]
    code, out = run_no_skips(tmp_path, cases, capsys=capsys)
    assert code == 1
    assert "ERROR tests.test_egress_httpx_fresh" in out and "collection failure" in out


def test_classes_and_subdirectories_count_for_their_file(tmp_path, capsys):
    cases = [
        ("tests.test_qa_airgap_happy_setup.TestWinConditionArtifacts", "test_a", "passed"),
        ("tests.portal.test_token_compliance", "test_b", "passed"),
    ]
    code, out = run_no_skips(
        tmp_path, cases, files=["tests/test_qa_airgap_happy_setup.py", "tests/portal/test_token_compliance.py"], capsys=capsys
    )
    assert code == 0, out


def test_an_unreadable_or_empty_junit_file_fails(tmp_path, capsys):
    bad = tmp_path / "bad.xml"
    bad.write_text("this is not xml", encoding="utf-8")
    assert check.main(["no-skips", str(bad), *FILES]) == 1
    assert "cannot read" in capsys.readouterr().out
    assert check.main(["no-skips", str(tmp_path / "missing.xml"), *FILES]) == 1
    assert check.main(["no-skips", str(write(tmp_path, [])), *FILES]) == 1  # a run with no cases at all


# --- CI-4: the negative control ---------------------------------------------------------------------

def control_cases(sentinel="failed", sentinel_message=UNPROVEN, declaration="passed", sentinel_text=""):
    cases = []
    if sentinel is not None:
        cases.append(("tests.test_egress_httpx", SENTINEL, sentinel, sentinel_message, sentinel_text))
    if declaration is not None:
        cases.append(("tests.test_egress_httpx", DECLARATION, declaration, "declared none, found none"))
    return cases


def run_control(tmp_path, cases, rc, capsys):
    path = write(tmp_path, cases, "neither-junit.xml")
    code = check.main(["negative-control", str(path), str(rc)])
    return code, capsys.readouterr().out


def test_negative_control_passes_only_when_the_sentinel_fails_loudly_and_the_declaration_passes(tmp_path, capsys):
    """CI-4.  exit 1, the sentinel failed saying UNPROVEN, the declaration passed."""
    code, out = run_control(tmp_path, control_cases(), 1, capsys)
    assert code == 0, out
    assert "ok" in out


@pytest.mark.parametrize("rc,meaning", [
    (0, "passed"),             # nothing failed: the proof can pass with no library to test
    (2, "collect"),            # interrupted or a collection error: nothing was proven
    (3, "internal"),
    (4, "node ids"),           # a mistyped node id, or its module failed to import (explicit node ids exit 4, not 2)
    (5, "no tests"),           # nothing collected: the node ids are wrong
])
def test_negative_control_fails_on_any_exit_code_but_one(tmp_path, capsys, rc, meaning):
    """CI-4.  Even when the XML looks exactly right: a green leg 5 must mean 'the sentinel failed', not
    'something else happened'.  Each message says what the code means."""
    code, out = run_control(tmp_path, control_cases(), rc, capsys)
    assert code == 1
    assert f"exited {rc}" in out and meaning in out.lower()


def test_negative_control_fails_when_the_sentinel_passed(tmp_path, capsys):
    """CI-4.  rc 1 can come from the declaration test; a passing sentinel is the vacuous pass."""
    code, out = run_control(tmp_path, control_cases(sentinel="passed", declaration="failed"), 1, capsys)
    assert code == 1
    assert "sentinel" in out and "passed" in out


def test_negative_control_fails_when_the_sentinel_failed_for_another_reason(tmp_path, capsys):
    """CI-4.  The word must be in the failure's MESSAGE.  pytest prints a failing test's whole source in
    the failure text, and the sentinel's source contains UNPROVEN, so matching the text would accept any
    failure inside it."""
    code, out = run_control(
        tmp_path,
        control_cases(sentinel_message="ModuleNotFoundError: No module named 'x'",
                      sentinel_text="def test_...():\n    assert importable, UNPROVEN"),
        1, capsys,
    )
    assert code == 1
    assert "UNPROVEN" in out and "another reason" in out


@pytest.mark.parametrize("sentinel", [None, "skipped", "error"])
def test_negative_control_fails_when_the_sentinel_did_not_run_or_errored(tmp_path, capsys, sentinel):
    code, out = run_control(tmp_path, control_cases(sentinel=sentinel), 1, capsys)
    assert code == 1
    assert "sentinel" in out


@pytest.mark.parametrize("declaration", [None, "failed", "skipped", "error"])
def test_negative_control_fails_when_the_declaration_did_not_pass(tmp_path, capsys, declaration):
    """CI-4.  If the declaration fails, the venv is not the 'neither' state it claims to be, and the
    sentinel's failure proves nothing about the other legs."""
    code, out = run_control(tmp_path, control_cases(declaration=declaration), 1, capsys)
    assert code == 1
    assert "declaration" in out


def test_negative_control_lists_every_problem_at_once(tmp_path, capsys):
    code, out = run_control(tmp_path, control_cases(sentinel="passed", declaration="failed"), 0, capsys)
    assert code == 1
    assert "exited 0" in out and "sentinel" in out and "declaration" in out


# --- the command line ---------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    [], ["frobnicate"], ["no-skips"], ["no-skips", "junit.xml"], ["negative-control"], ["negative-control", "junit.xml"],
    ["negative-control", "junit.xml", "one"], ["negative-control", "junit.xml", "1", "extra"],
])
def test_a_misused_command_line_is_a_usage_error(argv, capsys):
    """Exit 2 (argparse's own code for misuse), with the usage on stderr; never a pass."""
    assert check.main(argv) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_the_script_exits_with_the_checkers_code(tmp_path):
    """The workflow depends on the process exit status: a failing check must be a non-zero exit."""
    junit_path = write(tmp_path, clean_cases() + [("tests.test_egress_httpx", "test_x", "skipped", "r")])
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "no-skips", str(junit_path), *FILES], capture_output=True, text=True, cwd=tmp_path,
    )
    assert proc.returncode == 1 and "SKIPPED" in proc.stdout


# --- CI-5, CI-6: the workflow, the checker and the sentinel tests agree --------------------------------

def load_workflow():
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_the_workflow_is_valid_yaml_and_every_matrix_key_it_uses_exists_in_every_leg():
    """CI-6.  An expression that names a matrix key one leg lacks evaluates to an empty string in GitHub
    Actions, which turns a declaration or an install mode into nothing, silently."""
    text, data = load_workflow()
    legs = data["jobs"]["guard"]["strategy"]["matrix"]["include"]
    assert len(legs) == 5
    used = set(re.findall(r"\$\{\{\s*matrix\.(\w+)", text))
    assert used, "the workflow no longer uses its matrix"
    for leg in legs:
        missing = used - set(leg)
        assert not missing, f"leg {leg.get('leg')!r} lacks matrix keys {sorted(missing)}"
        assert all(isinstance(value, str) for value in leg.values()), leg  # "3.10" must not become 3.1
    assert [leg["install"] for leg in legs].count("neither") == 1
    assert {leg["expect"] for leg in legs if leg["install"] == "neither"} == {"none"}
    assert {leg["install"] for leg in legs} == {"floating", "lock", "both", "neither"}
    assert {leg["python"] for leg in legs} == {"3.10", "3.11"}


def _walk(node):
    """Every key and every scalar in a loaded YAML document (comments are not part of it)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)
    else:
        yield node


def test_the_workflow_has_no_escape_hatch():
    """CI-6.  No `|| true`, no continue-on-error: the fifth leg is only meaningful if red means red.
    (The workflow's header comment says so in words; this reads keys and commands, not comments.)"""
    text, data = load_workflow()
    items = [item for item in _walk(data) if isinstance(item, str)]
    assert "continue-on-error" not in items
    assert not [item for item in items if "|| true" in item or "||true" in item]
    assert data["permissions"] == {"contents": "read"}
    assert data["jobs"]["guard"]["strategy"]["fail-fast"] is False
    assert set(data[True] if True in data else data["on"]) >= {"pull_request", "push", "schedule", "workflow_dispatch"}


def test_every_file_the_workflow_runs_exists_and_the_checker_is_told_the_same_list():
    """CI-5.  The pytest line and the checker line both expand $EGRESS_TESTS, so a file cannot be run
    and not checked, or checked and not run."""
    text, data = load_workflow()
    files = data["env"]["EGRESS_TESTS"].split()
    assert len(files) == len(set(files)) and "tests/test_egress_ci_check.py" in files
    for rel in files:
        assert (ROOT / rel).is_file(), f"EGRESS_TESTS names {rel}, which does not exist"
        assert re.fullmatch(r"tests/test_\w+\.py", rel), rel
    steps = {step["name"]: step for step in data["jobs"]["guard"]["steps"]}
    run = steps["Egress tests (no failure, no skip, every file ran)"]["run"]
    assert "pytest $EGRESS_TESTS" in run
    assert re.search(r"egress_ci_check\.py no-skips egress-junit\.xml \$EGRESS_TESTS", run)
    assert "--junitxml=egress-junit.xml" in run


def test_the_negative_control_names_the_sentinel_and_declaration_the_checker_looks_for():
    """CI-5, F24.  The node ids in leg 5 are strings; if the sentinel is renamed the leg would collect
    nothing (exit 5, which the checker rejects) -- or, worse, the checker would look for a name that no
    longer exists.  All three must be the same two functions, and they must exist."""
    _, data = load_workflow()
    steps = {step["name"]: step for step in data["jobs"]["guard"]["steps"]}
    step = steps["Negative control (neither library) - the sentinel must fail"]
    assert step["if"] == "matrix.install == 'neither'"
    nodes = re.findall(r'"(tests/test_egress_httpx\.py::(\w+))"', step["run"])
    assert [name for _, name in nodes] == [SENTINEL, DECLARATION]
    assert (check.SENTINEL, check.DECLARATION) == (SENTINEL, DECLARATION)
    defined = {
        node.name for node in ast.walk(ast.parse((ROOT / "tests" / "test_egress_httpx.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
    }
    assert {SENTINEL, DECLARATION} <= defined
    assert "negative-control neither-junit.xml \"$rc\"" in step["run"]
    assert "--junitxml=neither-junit.xml" in step["run"]


def test_the_sentinel_message_carries_the_word_the_checker_requires():
    """CI-5.  The checker requires UNPROVEN in the failure message; the test's message must still say it."""
    source = (ROOT / "tests" / "test_egress_httpx.py").read_text(encoding="utf-8")
    assert re.search(r'UNPROVEN\s*=\s*\(\s*"egress coverage of the httpx family is UNPROVEN', source)
    assert "assert importable, UNPROVEN" in source


def test_the_legs_that_run_the_tests_are_every_leg_but_the_negative_control_and_declare_their_library_set():
    _, data = load_workflow()
    job = data["jobs"]["guard"]
    assert job["env"]["ARAIL_TEST_EXPECT_HTTPX_FAMILY"] == "${{ matrix.expect }}"
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Egress tests (no failure, no skip, every file ran)"]["if"] == "matrix.install != 'neither'"
    sdk = steps["Cloud SDK default clients are refused (anthropic, openai)"]
    assert sdk["if"] == "matrix.sdks != ''"
    assert sdk["env"]["ARAIL_TEST_EXPECT_SDKS"] == "${{ matrix.sdks }}"
    assert "tests/egress_ci_check.py no-skips sdk-junit.xml tests/test_egress_sdk_clients.py" in sdk["run"]
    assert (ROOT / "tests" / "test_egress_sdk_clients.py").is_file()


def test_a_green_run_shows_the_evidence_it_produced():
    """CI-6, REVIEW.md W5 (the workspace rule that a run must teach).  The tests print EVIDENCE tables (the
    brief's probe per library, huggingface_hub's refusals, the SDK chains) that pytest shows only for a
    failure, so a green run said nothing.  `-rP` shows the captured output of passing tests; and the SDK
    step installs the day's SDK versions after the 'What this leg installed' step has printed, so it
    prints them itself."""
    _, data = load_workflow()
    steps = {step["name"]: step for step in data["jobs"]["guard"]["steps"]}
    for name in ("Egress tests (no failure, no skip, every file ran)", "Cloud SDK default clients are refused (anthropic, openai)"):
        pytest_line = next(line for line in steps[name]["run"].splitlines() if "-m pytest" in line)
        reports = re.search(r"\s-r([A-Za-z]+)(\s|$)", pytest_line)
        assert reports and "P" in reports.group(1) and "s" in reports.group(1), (
            f"{name!r}: the pytest line does not pass -r with both s (skips) and P (output of passing tests): {pytest_line!r}"
        )
    run = steps["Cloud SDK default clients are refused (anthropic, openai)"]["run"]
    installed, tested = run.index("pip install anthropic openai"), run.index("-m pytest")
    printed = run[installed:tested]
    for package in ("anthropic", "openai", "httpx2"):
        assert f'"{package}"' in printed and "md.version" in printed, (
            f"the SDK step does not print the installed version of {package} between the install and the tests"
        )
