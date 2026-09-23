"""``kind: git`` — extract commit metadata from a local git repository.

Read-only: uses ``git log`` only (via the hardened runner), never a
checkout. License: GPL-2.0-only, redistributable — commit metadata (hash,
date, subject, message) is what's extracted, not the tree contents.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from arail.nucleus.corpus._git_env import run_git
from arail.nucleus.errors import DomainConfigError

LICENSE = "GPL-2.0-only"
REDISTRIBUTABLE = True

# Unit-separated log format: hash \x1f date(ISO) \x1f subject \x1f body, each
# record terminated by \x1e — avoids ambiguity from newlines inside commit
# messages, which a naive newline-split would mangle.
_LOG_FORMAT = "%H%x1f%aI%x1f%s%x1f%b%x1e"


def extract(repo_path: Path, *, since: Optional[str] = None,
           max_commits: Optional[int] = None) -> List[dict]:
    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        raise DomainConfigError(f"{repo_path} is not a git repository (no .git)")

    args = ["log", f"--pretty=format:{_LOG_FORMAT}"]
    if since:
        args.append(f"--since={since}")
    if max_commits:
        args.append(f"-n{int(max_commits)}")

    result = run_git(args, cwd=repo_path)
    if result.returncode != 0:
        raise DomainConfigError(
            f"git log failed in {repo_path}: {result.stderr.decode(errors='replace')[:500]}"
        )

    items: List[dict] = []
    raw = result.stdout.decode("utf-8", errors="replace")
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 4:
            continue
        sha, date_iso, subject, body = parts[0], parts[1], parts[2], parts[3]
        try:
            date = datetime.fromisoformat(date_iso).astimezone(timezone.utc).date().isoformat()
        except ValueError:
            continue
        items.append({
            "id": f"git:linux:{sha}",
            "kind": "git",
            "date": date,
            "subject": subject,
            "text": f"{subject}\n\n{body}".strip(),
            "sha": sha,
            "labels": {},
        })
    return items
