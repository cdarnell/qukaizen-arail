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
        r"_record_aerollm_bench|optimize-aerollm|aerollm_version|aerollm_bundle_(?:tag|sha256)|aerollm_commit"
    ),
    # file and path names
    re.compile(r"[\w./-]*aerollm[\w.-]*\.(?:sh|md|py|toml|jsonl|json|ya?ml)\b"),
    re.compile(r"/api/aerollm\b"),
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
    (re.compile(r"(?<=expected ')aerollm(?=')"),
     "autoresearch validation message: the backend id value users must type"),
    (re.compile(r"(?<=No module named ')aerollm(?=')"),
     "verbatim Python ImportError text quoting the frozen module name"),
    (re.compile(r"(?<=\[\")aerollm(?=\")"),
     "docs: a backend id in a quoted preference list"),
    (re.compile(r"(?<=`)aerollm(?=`)"),
     "backticked identifier in docs: the arailctl alias / an id users type"),
    (re.compile(r"git\+https://github\.com/cdarnell/aerollm@\w+"),
     "config/tuning.yml knob value: a pinned pip ref compared by string against "
     "its `choices` and persisted experiment state; changing it is a config "
     "migration, not a display fix (BUILD_LOG: architect feedback)"),
    (re.compile(r"Tier 1 deep reasoning via aeroLLM \(in-process"),
     "registry/store.py: the exact legacy built-in note, matched so persisted "
     "registries can be refreshed (F7); never shown to a user"),
    (re.compile(r"(?m)^\s*tags:\s*\[[^\]]*\baerollm\b[^\]]*\]"),
     "frontmatter tag list: a tag is a retrieval id, not prose"),
    (re.compile(r"(?m)^• AeroLLM ready \(release wheel 1\.0\.0\) — the 2nd inference\.$"),
     "docs/verification/aerollm-1.0.0-pin.md: captured v1.0.0 tool output "
     "recorded verbatim as evidence; exact line only"),
    (re.compile(r"(?m)^• AeroLLM \(2nd inference\) status$"),
     "docs/verification/aerollm-1.0.0-pin.md: captured v1.0.0 tool output "
     "recorded verbatim as evidence; exact line only"),
    (re.compile(r"\[teacher, aerollm\]"),
     "PKB frontmatter tag written for retrieval; a tag is an id, not prose"),
)

# Excluded wholesale (see ARCHITECTURE.md "Excluded paths").
EXCLUDED_FILES = {
    # implements the AERO_* -> QUEUELLM_* alias mapping itself
    "src/arail/nucleus/runtime_names.py",
    # maintainer packaging script pinned to the frozen bundle (ARCHITECTURE A6)
    "scripts/package-aerollm-bundle.sh",
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


def _strip_code_comments(code: str, *, js_comments: bool) -> str:
    code = _strip_block_comments(code, "/*", "*/")
    if js_comments:
        code = "\n".join(_strip_line_comment(x) for x in code.split("\n"))
    return code


_CODE_REGION = re.compile(r"(<script\b[^>]*>)(.*?)(</script>)|(<style\b[^>]*>)(.*?)(</style>)",
                          re.DOTALL | re.IGNORECASE)


def extract_markup(text: str, *, kind: str = "html"):
    """kind: ``html`` (comments only inside <script>/<style>, since ``/*`` and
    ``//`` are ordinary text in markup), ``js`` or ``css``."""
    text = _strip_block_comments(text, "<!--", "-->")
    if kind == "html":
        text = _strip_block_comments(text, "{#", "#}")   # Jinja comment
        def region(m: re.Match[str]) -> str:
            if m.group(1):
                return m.group(1) + _strip_code_comments(m.group(2), js_comments=True) + m.group(3)
            return m.group(4) + _strip_code_comments(m.group(5), js_comments=False) + m.group(6)
        text = _CODE_REGION.sub(region, text)
    else:
        text = _strip_code_comments(text, js_comments=(kind == "js"))
    for n, line in enumerate(text.splitlines(), 1):
        line = _drop_code_identifiers(line)
        if line.strip():
            yield n, line


def extract_shell(text: str):
    """Output lines: info/warn/err/echo/printf calls, heredoc bodies, and
    their backslash continuations. Comments are skipped."""
    out_call = re.compile(
        r"^\s*(?:[\w-]+\s*\(\)\s*\{\s*)?(?:info|warn|err|error|ok|die|echo|printf|"
        r"log|say|fail|success)\b|\bprint\(")   # also inline `python -c` output
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


# Markdown only. Code, not prose: a lowercase ``aerollm`` joined to other
# characters by ``- _ @ / .`` (repo, crate, path, test, knob, pip name), the
# frozen module in ``import aerollm``, the pyproject extra key ``aerollm = "``,
# ``grep -i aerollm`` and a quoted ``backend: aerollm`` value. A standalone
# word ``aerollm`` and every capitalised spelling are prose and stay flagged.
_MD_IDENTIFIER = re.compile(
    r"[\w.@~/-]*[-_@/~]aerollm(?![A-Za-z0-9])[\w.@/-]*|\baerollm[-_@/][\w.@/-]*|"
    r"\baerollm\.\w+|\bimport\s+aerollm\b|\baerollm\s*=\s*\"|"
    r"grep\s+-i\s+aerollm\b|\bbackend:\s*aerollm\b")


def extract_markdown(text: str):
    text = _strip_block_comments(text, "<!--", "-->")
    for n, line in enumerate(text.splitlines(), 1):
        line = _MD_IDENTIFIER.sub(" ", line)
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
        targets.append((p, "html"))
    for p in sorted((src / "portal" / "static").rglob("*")):
        if p.suffix == ".js":
            targets.append((p, "js"))
        elif p.suffix == ".css":
            targets.append((p, "css"))
    targets.append((ROOT / "arailctl", "shell"))
    for p in sorted((ROOT / "scripts").glob("*.sh")):
        if _rel(p) not in EXCLUDED_FILES:
            targets.append((p, "shell"))
    for p in sorted((ROOT / "lab" / "tools").rglob("*.py")):
        targets.append((p, "py"))
    for p in sorted(src.rglob("*.yaml")):
        targets.append((p, "yaml"))
    for p in sorted(src.rglob("*.md")):
        targets.append((p, "md"))
    # Docs the portal renders under /docs/ plus the root markdown it serves.
    # docs/archive/ is historical record and is never scanned.
    for p in sorted((ROOT / "docs").rglob("*.md")):
        if "archive" not in p.relative_to(ROOT / "docs").parts:
            targets.append((p, "md"))
    for name in ("BLUEPRINTS.md", "AGENTS.md"):
        targets.append((ROOT / name, "md"))
    for name in ("config/tuning.yml", "config/tuning-mlx.yml",
                 "catalog/models.toml"):
        targets.append((ROOT / name, "yaml"))
    return [(p, k) for p, k in targets
            if p.exists() and _rel(p) not in EXCLUDED_FILES]


def extract(kind: str, text: str):
    if kind == "py":
        return extract_py(text)
    if kind in ("html", "js", "css"):
        return extract_markup(text, kind=kind)
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
        if not violations_in_text(chunk):
            continue
        # A multi-line literal is reported at the line that actually matches.
        rows = chunk.split("\n")
        located = [(i, r) for i, r in enumerate(rows) if violations_in_text(r)]
        if not located:
            located = [(0, rows[0])]
        for offset, row in located:
            hits.append((line_no + offset, row.strip()[:120]))
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
    ("html", "<span>Deep · aeroLLM</span>"),
    # `/*` is ordinary text in markup; it must not blank the lines after it.
    ("html", '<a href="/api/*">files</a>\n<span>AeroLLM</span>'),
    ("html", '<a title="served by AeroLLM" href="/x">y</a>'),
    ("html", "flashStatus('deep model (aeroLLM) not built')  // note"),
    ("shell", 'info "AeroLLM ready"'),
    ("shell", 'echo "building AeroLLM"'),
    ("shell", '"$PY" -c "\nprint(f\'bundle: aerollm {ver}\')\n"'),
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
    ("html", "<!-- AeroLLM comment --><p>ok</p>"),
    ("html", "{# AeroLLM jinja\n   comment #}<p>ok</p>"),
    ("css", "/* AeroLLM css */ .a{}"),
    ("js", "var x = 1; // AeroLLM comment"),
    ("html", "<script>var x = 1; // AeroLLM comment\n</script>"),
    ("html", "<div data-view=\"aerollm-mlx\" id=\"aerollm-card\">x</div>"),
    ("html", "runtime: 'aerollm',"),
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


@pytest.mark.parametrize("sample", [
    "Run the aerollm engine.",
    "Deep model: AeroLLM.",
    "label: 'aerollm'",
])
def test_guard_markdown_flags_prose_spellings(sample):
    assert scan_text("md", sample)


@pytest.mark.parametrize("sample", [
    "see `aerollm-api` and ~/ProJects/qukaizen-aerollm/NOTICE",
    "run test_aerollm_compute_source",
    ">>> import aerollm",
    "backend: aerollm",
])
def test_guard_markdown_passes_identifiers(sample):
    assert not scan_text("md", sample)


def test_guard_scans_docs_tools_and_all_scripts():
    rels = {_rel(p) for p, _ in scan_targets()}
    assert "BLUEPRINTS.md" in rels and "lab/tools/benchmark_models.py" in rels
    assert "docs/world-forge.md" in rels
    assert not any(r.startswith("docs/archive/") for r in rels)
    assert "scripts/blueprint.sh" in rels
    assert "scripts/package-aerollm-bundle.sh" not in rels


def test_guard_runs_fast_and_scans_real_files():
    import time
    t0 = time.perf_counter()
    targets = scan_targets()
    scan_repo()
    assert time.perf_counter() - t0 < 5.0
    kinds = {k for _p, k in targets}
    assert {"py", "html", "js", "shell", "yaml", "md"} <= kinds
    assert any(_rel(p) == "src/arail/portal/templates/chat.html" for p, _ in targets)
    assert any(_rel(p) == "arailctl" for p, _ in targets)
