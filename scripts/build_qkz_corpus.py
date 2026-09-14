#!/usr/bin/env python3
"""Build the QuKaiZen expert training corpus — instruction pairs, never raw dumps.

A2 of sprints/2026-07-24-qkz-expert-2b/ARCHITECTURE.md.

DESIGN NOTES (the non-obvious parts)
------------------------------------
**Instruction pairs, not raw files.** Fine-tuning a small instruct model on raw
source dumps degrades instruction-following (VISION disconfirmer 3). So we
*derive* Q&A from structure that already carries intent: module/function
docstrings, and Markdown sections under their headings.

**`git ls-files` is the file list.** It respects each repo's `.gitignore` for
free, so `.venv`, `node_modules`, `lab/` runtime state and downloaded weights
are excluded by construction rather than by a hand-maintained denylist that
drifts.

**Secrets are treated as radioactive.** A fine-tune memorizes what it is shown,
and a leaked key cannot be un-trained — you would have to retrain. So there are
two independent layers: a path denylist AND a content scan on every extracted
pair. Anything suspicious is dropped, and the count is reported (a silent drop
would hide a problem).

**Deterministic.** Fixed seed, sorted inputs, and a `corpus_sha256` over the
emitted records so a training run can be tied to exactly this data.

Usage:
    python scripts/build_qkz_corpus.py --out lab/data/corpus
    python scripts/build_qkz_corpus.py --out /tmp/c --repos ~/ProJects/qukaizen-arail
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REPOS = [
    "qukaizen-aerollm",
    "qukaizen-arail",
    "qukaizen-ddac",
    "qukaizen-nucleus",
]

# Layer 1: never read these paths at all.
SECRET_PATH_PATTERNS = re.compile(
    r"(^|/)("
    r"\.env(\..*)?|secrets?(\.env|\.ya?ml|\.json)?|lab\.conf|credentials?"
    r"|id_rsa|id_ed25519|.*\.pem|.*\.key|.*\.p12|.*\.pfx|.*\.keystore"
    r"|\.netrc|\.npmrc|\.pypirc"
    r")$",
    re.IGNORECASE,
)

# Layer 2: drop any extracted text that smells like a live credential.
SECRET_CONTENT_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),              # OpenAI-style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),       # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),                # AWS access key id
    re.compile(r"\bhf_[A-Za-z0-9]{20,}"),              # HuggingFace
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),     # Slack
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token)"
               r"\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
]

MIN_ANSWER_CHARS = 80      # below this a "pair" teaches nothing
MAX_ANSWER_CHARS = 2400    # ~600 tokens; keeps pairs inside a 1024-token
                           # window WITH the question + template overhead.
                           # The A3 calibration truncated answers up to 1394
                           # tokens, teaching incomplete responses.
MIN_QUESTION_CHARS = 10
# An answer that is pure code/diagram with no explanation is not instructional.
MIN_PROSE_CHARS = 120

# Paths excluded for CONTENT-QUALITY reasons (not secrets).
#
# `sprints/` is the important one: those are historical process artifacts —
# plans that were superseded, descoped, or never built. The 2026-07-23
# assessment found shipped docs describing whole subsystems that do not exist
# on disk. Training the expert on them teaches confident wrong answers about
# how QuKaiZen works, which is the exact failure this sprint exists to prevent.
LOW_VALUE_PATH_PATTERNS = re.compile(
    r"(^|/)("
    r"sprints?|retros?|learnings|\.claude|\.github|node_modules|vendor"
    r"|site-packages|migrations|fixtures|__pycache__"
    r")(/|$)",
    re.IGNORECASE,
)
# Test code teaches test scaffolding, not the product.
TEST_PATH_PATTERNS = re.compile(
    r"(^|/)(tests?|testing|conftest\.py)(/|$)|(^|/)test_[^/]*\.py$"
    r"|[^/]*_test\.py$",
    re.IGNORECASE,
)
# Section headings that make useless questions ("3.1", "Step 2", "TODO").
_JUNK_HEADING = re.compile(
    r"^([\d.\s]+|step\s*\d+.*|phase\s*\d+.*|appendix.*|todo.*|notes?|misc.*"
    r"|table of contents|toc|changelog|license|index)$",
    re.IGNORECASE,
)


@dataclass
class SourceStats:
    repo: str
    files_scanned: int = 0
    pairs_python: int = 0
    pairs_markdown: int = 0
    skipped_secret_path: int = 0
    dropped_secret_content: int = 0
    skipped_unreadable: int = 0
    skipped_low_value_path: int = 0
    skipped_test_path: int = 0
    dropped_junk_heading: int = 0
    dropped_no_prose: int = 0

    @property
    def pairs(self) -> int:
        return self.pairs_python + self.pairs_markdown


@dataclass
class Corpus:
    records: list[dict] = field(default_factory=list)
    stats: list[SourceStats] = field(default_factory=list)
    seen_hashes: set[str] = field(default_factory=set)

    def add(self, question: str, answer: str, *, source: str, kind: str,
            stat: SourceStats) -> bool:
        q, a = question.strip(), answer.strip()
        if len(q) < MIN_QUESTION_CHARS or len(a) < MIN_ANSWER_CHARS:
            return False
        if len(a) > MAX_ANSWER_CHARS:
            a = a[:MAX_ANSWER_CHARS].rsplit("\n", 1)[0]
        blob = f"{q}\n{a}"
        if looks_secret(blob):
            stat.dropped_secret_content += 1
            return False
        h = hashlib.sha256(blob.encode()).hexdigest()
        if h in self.seen_hashes:
            return False
        self.seen_hashes.add(h)
        self.records.append({
            "question": q, "answer": a, "source": source, "kind": kind,
        })
        return True


def looks_secret(text: str) -> bool:
    return any(p.search(text) for p in SECRET_CONTENT_PATTERNS)


def prose_chars(text: str) -> int:
    """Characters outside fenced code blocks — how much of an answer actually
    *explains* rather than just dumping code or an ASCII diagram."""
    total, in_fence = 0, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            total += len(line.strip())
    return total


def is_junk_heading(heading: str) -> bool:
    h = re.sub(r"^[\d.)\s]+", "", heading.strip()).strip()
    return not h or bool(_JUNK_HEADING.match(h)) or len(h) < 4


def is_secret_path(rel: str) -> bool:
    return bool(SECRET_PATH_PATTERNS.search(rel))


def git_tracked_files(repo: Path) -> list[str]:
    """Tracked files only — inherits the repo's .gitignore."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files"],
            capture_output=True, text=True, timeout=60, check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    return sorted(f for f in out.stdout.splitlines() if f.strip())


# ── extraction ──────────────────────────────────────────────────────

def extract_python(path: Path, rel: str, repo_name: str,
                   corpus: Corpus, stat: SourceStats) -> None:
    try:
        src = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        stat.skipped_unreadable += 1
        return
    try:
        tree = ast.parse(src)
    except SyntaxError:
        stat.skipped_unreadable += 1
        return

    module = rel.replace("/", ".").removesuffix(".py")

    if (doc := ast.get_docstring(tree)):
        if corpus.add(
            f"In the QuKaiZen {repo_name} project, what is `{module}` for?",
            doc, source=f"{repo_name}:{rel}", kind="module_docstring", stat=stat,
        ):
            stat.pairs_python += 1

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        doc = ast.get_docstring(node)
        if not doc:
            continue
        if isinstance(node, ast.ClassDef):
            q = (f"In QuKaiZen {repo_name}, what does the `{node.name}` class "
                 f"(in `{module}`) do?")
        else:
            try:
                args = ", ".join(a.arg for a in node.args.args)
            except Exception:  # noqa: BLE001
                args = ""
            q = (f"In QuKaiZen {repo_name}, what does `{node.name}({args})` "
                 f"in `{module}` do?")
        if corpus.add(q, doc, source=f"{repo_name}:{rel}",
                      kind="api_docstring", stat=stat):
            stat.pairs_python += 1


_H_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


def extract_markdown(path: Path, rel: str, repo_name: str,
                     corpus: Corpus, stat: SourceStats) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        stat.skipped_unreadable += 1
        return

    # Drop YAML front-matter so it doesn't become an "answer".
    if text.startswith("---\n") and (end := text.find("\n---", 4)) != -1:
        text = text[end + 4:]

    doc_title = Path(rel).stem.replace("-", " ").replace("_", " ")
    heading, buf = None, []

    def flush() -> None:
        nonlocal heading, buf
        if heading and buf:
            if is_junk_heading(heading):
                stat.dropped_junk_heading += 1
            else:
                body = "\n".join(buf).strip()
                if prose_chars(body) < MIN_PROSE_CHARS:
                    # Pure code fence / ASCII diagram — not an instructional answer.
                    stat.dropped_no_prose += 1
                else:
                    q = (f"In QuKaiZen {repo_name} ({doc_title}), "
                         f"{heading_to_question(heading)}")
                    if corpus.add(q, body, source=f"{repo_name}:{rel}",
                                  kind="doc_section", stat=stat):
                        stat.pairs_markdown += 1
        heading, buf = None, []

    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and (m := _H_RE.match(line)):
            flush()
            heading = m.group(2).strip()
            continue
        if heading is not None:
            buf.append(line)
    flush()


def heading_to_question(heading: str) -> str:
    h = heading.strip().rstrip("?").strip()
    if re.match(r"^(what|why|how|when|where|who|which)\b", h, re.IGNORECASE):
        return h[0].lower() + h[1:] + "?"
    return f"explain: {h}"


# ── build ───────────────────────────────────────────────────────────

def build(repos: list[Path], *, seed: int = 1729,
          holdout: float = 0.10) -> tuple[Corpus, list[dict], list[dict]]:
    corpus = Corpus()
    for repo in repos:
        name = repo.name.replace("qukaizen-", "")
        stat = SourceStats(repo=name)
        for rel in git_tracked_files(repo):
            if is_secret_path(rel):
                stat.skipped_secret_path += 1
                continue
            if LOW_VALUE_PATH_PATTERNS.search(rel):
                stat.skipped_low_value_path += 1
                continue
            if TEST_PATH_PATTERNS.search(rel):
                stat.skipped_test_path += 1
                continue
            p = repo / rel
            if not p.is_file():
                continue
            if rel.endswith(".py"):
                stat.files_scanned += 1
                extract_python(p, rel, name, corpus, stat)
            elif rel.endswith(".md"):
                stat.files_scanned += 1
                extract_markdown(p, rel, name, corpus, stat)
        corpus.stats.append(stat)

    records = sorted(corpus.records, key=lambda r: (r["source"], r["question"]))
    random.Random(seed).shuffle(records)
    n_hold = max(1, int(len(records) * holdout)) if records else 0
    return corpus, records[n_hold:], records[:n_hold]


DEFAULT_MODEL = "/Users/Shared/models/gemma-4-e2b-it-OptiQ-4bit"


class TemplateUnavailable(SystemExit):
    """Raised when the model's real chat template cannot be loaded."""


def load_chat_formatter(model_path: str):
    """Return ``fn(question, answer) -> str`` using the MODEL'S OWN template.

    Why this is not optional: the A3 calibration run
    (``sprints/2026-07-24-qkz-expert-2b/CALIBRATION.md``) trained on a
    hardcoded Gemma 2/3 ``<start_of_turn>`` format while this checkpoint uses
    the Gemma 4 Canonical template (``<|turn>…<turn|>``, role ``model``). Loss
    fell beautifully and the model's output collapsed into a repetition loop,
    because every example taught a turn structure it does not use.

    So: derive the format from the tokenizer, and **hard-fail** if we cannot.
    Silently emitting a guessed format is the bug we already paid for.
    """
    try:
        from transformers import AutoTokenizer  # type: ignore
    except ImportError as exc:
        raise TemplateUnavailable(
            "REFUSING TO BUILD: transformers is unavailable, so the model's real "
            "chat template cannot be read. Emitting a guessed format is exactly "
            "what produced the degenerate calibration model (see CALIBRATION.md). "
            "Install transformers, or pass --model pointing at a local checkpoint."
        ) from exc

    try:
        tok = AutoTokenizer.from_pretrained(model_path)
    except Exception as exc:  # noqa: BLE001
        raise TemplateUnavailable(
            f"REFUSING TO BUILD: could not load a tokenizer from {model_path!r} "
            f"({type(exc).__name__}: {exc}). The corpus format must come from the "
            "model, not from a hardcoded guess."
        ) from exc

    if not getattr(tok, "chat_template", None):
        raise TemplateUnavailable(
            f"REFUSING TO BUILD: {model_path!r} exposes no chat_template. "
            "Cannot format training text faithfully."
        )

    def fmt(question: str, answer: str) -> str:
        return tok.apply_chat_template(
            [{"role": "user", "content": question},
             {"role": "assistant", "content": answer}],
            tokenize=False,
        )

    return fmt, tok


def template_fingerprint(fmt) -> dict:
    """Record which turn markers the corpus actually used, so a future
    model/template swap is detectable from the receipt alone."""
    sample = fmt("Q", "A")
    return {
        "sample": sample,
        "markers": sorted({m for m in ("<|turn>", "<turn|>", "<start_of_turn>",
                                       "<end_of_turn>", "<|channel>", "<bos>")
                           if m in sample}),
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the QuKaiZen expert corpus.")
    ap.add_argument("--out", default="lab/data/corpus", help="output directory")
    ap.add_argument("--repos", nargs="*", default=None,
                    help="repo paths (default: the four QuKaiZen repos in ~/ProJects)")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="checkpoint whose chat template formats the corpus")
    ap.add_argument("--seed", type=int, default=1729)
    ap.add_argument("--holdout", type=float, default=0.10)
    args = ap.parse_args(argv)

    if args.repos:
        repos = [Path(r).expanduser().resolve() for r in args.repos]
    else:
        base = Path.home() / "ProJects"
        repos = [base / r for r in DEFAULT_REPOS]
    repos = [r for r in repos if r.is_dir()]
    if not repos:
        print("no repos found", file=sys.stderr)
        return 1

    corpus, train, valid = build(repos, seed=args.seed, holdout=args.holdout)
    if not train:
        print("corpus is empty — refusing to write", file=sys.stderr)
        return 1

    # Format with the MODEL'S template — never a hardcoded guess (see
    # CALIBRATION.md finding 1). Hard-fails if it cannot be read.
    fmt, _tok = load_chat_formatter(args.model)
    fp = template_fingerprint(fmt)

    out = Path(args.out)
    write_jsonl(out / "train.jsonl",
                [{"text": fmt(r["question"], r["answer"])} for r in train])
    write_jsonl(out / "valid.jsonl",
                [{"text": fmt(r["question"], r["answer"])} for r in valid])
    # Provenance sidecar: which record came from which file (NOT trained on).
    write_jsonl(out / "provenance.jsonl", train + valid)

    digest = hashlib.sha256()
    for r in train + valid:
        digest.update(json.dumps(r, sort_keys=True).encode())
    corpus_sha = digest.hexdigest()

    receipt = {
        "corpus_sha256": corpus_sha,
        "model": args.model,
        "chat_template": fp,
        "seed": args.seed,
        "holdout": args.holdout,
        "records_total": len(train) + len(valid),
        "records_train": len(train),
        "records_holdout": len(valid),
        "sources": [
            {
                "repo": s.repo, "files_scanned": s.files_scanned,
                "pairs": s.pairs, "pairs_python": s.pairs_python,
                "pairs_markdown": s.pairs_markdown,
                "skipped_secret_path": s.skipped_secret_path,
                "dropped_secret_content": s.dropped_secret_content,
                "skipped_unreadable": s.skipped_unreadable,
                "skipped_low_value_path": s.skipped_low_value_path,
                "skipped_test_path": s.skipped_test_path,
                "dropped_junk_heading": s.dropped_junk_heading,
                "dropped_no_prose": s.dropped_no_prose,
            }
            for s in corpus.stats
        ],
    }
    (out / "RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")

    print(f"chat template : {', '.join(fp['markers'])}")
    print(f"corpus_sha256 : {corpus_sha}")
    print(f"train / holdout: {len(train)} / {len(valid)}")
    for s in corpus.stats:
        print(f"  {s.repo:10s} files={s.files_scanned:5d} pairs={s.pairs:5d} "
              f"(py={s.pairs_python} md={s.pairs_markdown}) "
              f"secret_paths_skipped={s.skipped_secret_path} "
              f"skipped(sprints/tests)={s.skipped_low_value_path}/{s.skipped_test_path} "
              f"dropped(junk/nocode-prose)={s.dropped_junk_heading}/{s.dropped_no_prose}")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
