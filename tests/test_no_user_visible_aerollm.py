"""Guard: no user-visible "AeroLLM" display string in arail.

The engine was renamed QueueLLM. Every string a person *reads* (portal pages,
``arailctl`` help, script output, error and status messages, seeded content)
must say QueueLLM. Strings a *machine* reads stay frozen: env var names,
package and module names, backend and registry ids, JSON values, script file
names, DOM ids, the bundled NOTICE. See
sprints/2026-10-07-queuellm-display-rename/ARCHITECTURE.md ("The rule").

The scanner is token-aware: it extracts only user-visible text (string
constants, markup text and attributes, shell output lines, YAML/Markdown
prose), strips comments and docstrings, removes frozen tokens, skips
identifier-shaped literals and migration notes, then fails on any remaining
``aero\\s*llm``. The self-tests at the bottom keep a broken extractor from
producing a vacuously green guard.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

AERO = re.compile(r"aero\s*llm", re.IGNORECASE)

# Rule 2: frozen tokens. Machine-read; removed from a string before matching.
FROZEN_TOKENS = [
    # env vars (AEROLLM_MODEL, AERO_*, ARAIL_AEROLLM_REPO, LAB_SHOW_AEROLLM ...)
    re.compile(r"[A-Z0-9_]*AERO(?:LLM)?_[A-Z0-9_]*"),
    re.compile(r"[A-Z0-9_]*_AEROLLM\b"),
    # package, module, class, ids, keys, skill id
    re.compile(
        r"libaerollm_api|aerollm[_-]api|AeroLLMBackend|tier1-aerollm|"
        r"backend_aerollm|aerollm-mlx|aerollm-cuda|tn-arch-aerollm|"
        r"show_aerollm|aerollm_status|aerollm_model|aerollm_preload_loop|"
        r"_record_aerollm_bench|optimize-aerollm"
    ),
    # file and path names
    re.compile(r"[\w./-]*aerollm[\w.-]*\.(?:sh|md|py|toml)\b"),
    re.compile(r"research/aerollm|THIRD-PARTY-LICENSES/aerollm"),
    re.compile(r"sprints/[\w./-]*aerollm[\w./-]*"),
]

# Rule 3: a literal that is entirely identifier-shaped and lowercase is an id.
IDENTIFIER_SHAPED = re.compile(r"^[a-z0-9_:.\-/]*aerollm[a-z0-9_:.\-/]*$")

# Rule 5: migration notes help users who still have the old name in .env.
MIGRATION_NOTES = [
    re.compile(r"formerly\s+AeroLLM", re.IGNORECASE),
    re.compile(r"AeroLLM\s+was\s+renamed", re.IGNORECASE),
    re.compile(r"renamed\s+from\s+AeroLLM", re.IGNORECASE),
]

# Reviewer-visible exceptions: (regex removed from the text, reason). Extending
# this list needs a reason; never rename a frozen id to get green.
ALLOWLIST: tuple[tuple[re.Pattern[str], str], ...] = (
    # the ``arailctl`` CLI alias is a command name users type
    (re.compile(r"benchmark,\s*aerollm"), "arailctl alias `aerollm` is a command"),
    (re.compile(r"(?<=['\"])aerollm(?=['\"])"),
     "backend id quoted inside a validation message (the value users must type)"),
    (re.compile(r"git\+https://github\.com/cdarnell/aerollm@\w+"),
     "config/tuning.yml knob value: a pinned pip ref compared by string against "
     "its `choices` and persisted experiment state; changing it is a config "
     "migration, not a display fix (BUILD_LOG: architect feedback)"),
    (re.compile(r"\[teacher, aerollm\]"),
     "PKB frontmatter tag written for retrieval; a tag is an id, not prose"),
)

# Excluded wholesale (see ARCHITECTURE.md "Excluded paths").
EXCLUDED_FILES = {
    # implements the AERO_* -> QUEUELLM_* alias mapping itself
    "src/arail/nucleus/runtime_names.py",
}


def _strip_frozen(text: str) -> str:
    for rx in FROZEN_TOKENS:
        text = rx.sub(" ", text)
    for rx, _reason in ALLOWLIST:
        text = rx.sub(" ", text)
    for rx in MIGRATION_NOTES:
        text = rx.sub(" ", text)
    return text


def violations_in_text(text: str) -> list[str]:
    """Return the offending fragments of one extracted user-visible string."""
    if IDENTIFIER_SHAPED.match(text.strip().strip("\"'`,; ")):
        return []
    cleaned = _strip_frozen(text)
    return [m.group(0) for m in AERO.finditer(cleaned)]


# ── Extractors: each yields (line_number, text) ──────────────────────────
def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def extract_py(source: str):
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip):
            yield node.lineno, node.value


def _strip_block_comments(text: str, opener: str, closer: str) -> str:
    """Blank a block comment but keep its newlines so line numbers hold."""
    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    return re.sub(re.escape(opener) + r".*?" + re.escape(closer), blank, text,
                  flags=re.DOTALL)


def _strip_line_comment(line: str) -> str:
    """Drop a ``//`` comment that is outside quotes and is not a URL.

    An apostrophe only opens a quote when it is not inside a word, so prose
    such as "isn't" does not hide a trailing comment.
    """
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = ""
            continue
        if ch in "\"`" or (ch == "'" and (i == 0 or not line[i - 1].isalnum())):
            quote = ch
            continue
        if line.startswith("//", i) and (i == 0 or line[i - 1] != ":"):
            return line[:i]
    return line


_QUOTED_IDENT = re.compile(
    r"([\"'`])[a-z0-9_:.\-/]*aerollm[a-z0-9_:.\-/]*\1")
_SELECTOR_OR_PROPERTY = re.compile(r"(?<=[#.])[\w-]*aerollm[\w-]*")
_OBJECT_KEY = re.compile(r"\baerollm\s*:")


def _drop_code_identifiers(line: str) -> str:
    """Rule 3 for markup: identifier-shaped quoted values (ids, data-view,
    runtime), CSS selectors, property accesses and object keys are code."""
    line = _QUOTED_IDENT.sub(" ", line)
    line = _SELECTOR_OR_PROPERTY.sub(" ", line)
    return _OBJECT_KEY.sub(" ", line)


def extract_markup(text: str, *, js_comments: bool = True):
    text = _strip_block_comments(text, "<!--", "-->")
    text = _strip_block_comments(text, "/*", "*/")
    for n, line in enumerate(text.splitlines(), 1):
        if js_comments:
            line = _strip_line_comment(line)
        line = _drop_code_identifiers(line)
        if line.strip():
            yield n, line


def extract_shell(text: str):
    """Output lines: info/warn/err/echo/printf calls, heredoc bodies, and
    their backslash continuations. Comments are skipped."""
    out_call = re.compile(
        r"^\s*(?:[\w-]+\s*\(\)\s*\{\s*)?(?:info|warn|err|error|ok|die|echo|printf|"
        r"log|say|fail|success)\b")
    heredoc_end: str | None = None
    continued = False
    for n, line in enumerate(text.splitlines(), 1):
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            else:
                yield n, line
            continue
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        stripped = line.strip()
        is_comment = stripped.startswith("#")
        if m and re.match(r"^\s*(?:cat|printf|echo)\b", line):
            heredoc_end = m.group(1)
            continue
        if not is_comment and (out_call.match(line) or continued):
            yield n, line
        continued = (not is_comment) and line.rstrip().endswith("\\")


def extract_yaml_toml(text: str):
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        line = re.sub(r"\s#\s.*$", "", line)
        # Judge the value, so `backend: aerollm` reads as an identifier.
        line = re.sub(r"^\s*(?:-\s*)?[\w.\-\"']+\s*[:=]\s*(?=\S)", "", line)
        if line.strip():
            yield n, line


def extract_markdown(text: str):
    text = _strip_block_comments(text, "<!--", "-->")
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip():
            yield n, line


# ── Which files each extractor owns ──────────────────────────────────────
def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def scan_targets() -> list[tuple[Path, str]]:
    src = ROOT / "src" / "arail"
    targets: list[tuple[Path, str]] = []
    for p in sorted(src.rglob("*.py")):
        targets.append((p, "py"))
    for p in sorted((src / "portal" / "templates").rglob("*.html")):
        targets.append((p, "markup"))
    for p in sorted((src / "portal" / "static").rglob("*")):
        if p.suffix == ".js":
            targets.append((p, "markup"))
        elif p.suffix == ".css":
            targets.append((p, "css"))
    for name in ("arailctl", "scripts/setup.sh", "scripts/upgrade.sh",
                 "scripts/build-aerollm.sh", "scripts/blueprint.sh"):
        targets.append((ROOT / name, "shell"))
    for p in sorted(src.rglob("*.yaml")):
        targets.append((p, "yaml"))
    for p in sorted(src.rglob("*.md")):
        targets.append((p, "md"))
    targets.append((ROOT / "docs" / "cli.md", "md"))
    for name in ("config/tuning.yml", "config/tuning-mlx.yml",
                 "catalog/models.toml"):
        targets.append((ROOT / name, "yaml"))
    return [(p, k) for p, k in targets
            if p.exists() and _rel(p) not in EXCLUDED_FILES]


def extract(kind: str, text: str):
    if kind == "py":
        return extract_py(text)
    if kind == "markup":
        return extract_markup(text)
    if kind == "css":
        return extract_markup(text, js_comments=False)
    if kind == "shell":
        return extract_shell(text)
    if kind == "yaml":
        return extract_yaml_toml(text)
    if kind == "md":
        return extract_markdown(text)
    raise ValueError(kind)


def scan_text(kind: str, text: str) -> list[tuple[int, str]]:
    hits = []
    for line_no, chunk in extract(kind, text):
        for frag in violations_in_text(chunk):
            hits.append((line_no, chunk.strip()[:120] or frag))
    return hits


def scan_repo() -> list[str]:
    found = []
    for path, kind in scan_targets():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, snippet in scan_text(kind, text):
            found.append(f"{_rel(path)}:{line_no}: {snippet}")
    return found


# ── The guard ────────────────────────────────────────────────────────────
@pytest.mark.xfail(
    strict=True,
    reason="rename in progress: sprint 2026-10-07-queuellm-display-rename "
           "BUILD_LOG steps 3-6; flipped to a normal test in step 7",
)
def test_no_user_visible_aerollm():
    found = scan_repo()
    assert not found, (
        f"{len(found)} user-visible AeroLLM string(s); say QueueLLM "
        "(env vars, ids, package names stay frozen):\n" + "\n".join(found)
    )


# ── Self-tests: the extractor must flag and pass what it should ──────────
@pytest.mark.parametrize("kind,sample", [
    ("py", 'msg = "AeroLLM isn\'t ready"'),
    ("py", 'label = f"{x} @ aeroLLM"'),
    ("markup", "<span>Deep · aeroLLM</span>"),
    ("markup", '<a title="served by AeroLLM" href="/x">y</a>'),
    ("markup", "flashStatus('deep model (aeroLLM) not built')  // note"),
    ("shell", 'info "AeroLLM ready"'),
    ("shell", 'echo "building AeroLLM"'),
    ("shell", "cat <<EOF\n  deep <op>  AeroLLM 2nd inference\nEOF"),
    ("yaml", "description: AeroLLM layer streaming"),
    ("md", "Run the AeroLLM engine."),
])
def test_guard_flags_user_visible_samples(kind, sample):
    assert scan_text(kind, sample), f"extractor missed: {sample!r}"


@pytest.mark.parametrize("kind,sample", [
    ("py", 'backend = "aerollm"'),
    ("py", 'k = "AEROLLM_MODEL"'),
    ("py", 'import_line = "from aerollm_api import x"'),
    ("py", 'entry = "tier1-aerollm"'),
    ("py", '# AeroLLM comment\nx = 1'),
    ("py", 'def f():\n    """AeroLLM docstring."""\n    return 1'),
    ("py", 'note = "formerly AeroLLM"'),
    ("markup", "<!-- AeroLLM comment --><p>ok</p>"),
    ("markup", "/* AeroLLM css */ .a{}"),
    ("markup", "var x = 1; // AeroLLM comment"),
    ("markup", "<div data-view=\"aerollm-mlx\" id=\"aerollm-card\">x</div>"),
    ("markup", "runtime: 'aerollm',"),
    ("shell", "# AeroLLM comment"),
    ("shell", 'echo "set AEROLLM_MODEL=foo"'),
    ("shell", 'source scripts/build-aerollm.sh'),
    ("shell", 'info "AeroLLM was renamed QueueLLM; set QUEUELLM_X"'),
    ("yaml", "# AeroLLM comment"),
    ("yaml", "backend: aerollm"),
    ("md", "<!-- AeroLLM -->"),
])
def test_guard_passes_frozen_and_non_visible_samples(kind, sample):
    got = scan_text(kind, sample)
    assert not got, f"false positive on {sample!r}: {got}"


def test_guard_runs_fast_and_scans_real_files():
    import time
    t0 = time.perf_counter()
    targets = scan_targets()
    scan_repo()
    assert time.perf_counter() - t0 < 5.0
    kinds = {k for _p, k in targets}
    assert {"py", "markup", "shell", "yaml", "md"} <= kinds
    assert any(_rel(p) == "src/arail/portal/templates/chat.html" for p, _ in targets)
    assert any(_rel(p) == "arailctl" for p, _ in targets)
