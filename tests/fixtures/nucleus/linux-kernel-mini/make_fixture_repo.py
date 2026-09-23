"""Builds a fully synthetic tiny git repo + vulns mapping at test time.

No real kernel text enters this MIT repo's fixtures (GPL-2.0 must not) —
every commit, subsystem name, and file below is invented for this fixture.
Loaded via importlib (this directory's name isn't a valid Python package
identifier) by tests/nucleus/test_corpus_stage.py and later the Gate A
end-to-end test (commit 22).

Shape (sprints/2026-09-23-nucleus-sprint-1/ARCHITECTURE.md §7.1):
    - a fake MAINTAINERS file, 6 subsystems
    - 50 commits spanning a fake cutoff, 20 of them strictly after it
    - 8 of the 50 are "CVE" commits, mapped in vulns/mapping.json
    - cert_n=20, dev_fraction=0.1 over the 30 pre-cutoff commits (~3 dev,
      ~27 train), matching linux-kernel-mini.yaml
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

SUBSYSTEMS = ["netdev", "blockdev", "fsext", "mmcore", "schedcore", "cryptoapi"]
CUTOFF = "2026-06-01"
N_COMMITS = 50
N_AFTER_CUTOFF = 20
N_CVE_COMMITS = 8

MAINTAINERS_TEMPLATE = """\
NETDEV SUBSYSTEM (FIXTURE)
F:      netdev/

BLOCKDEV SUBSYSTEM (FIXTURE)
F:      blockdev/

FSEXT SUBSYSTEM (FIXTURE)
F:      fsext/

MMCORE SUBSYSTEM (FIXTURE)
F:      mmcore/

SCHEDCORE SUBSYSTEM (FIXTURE)
F:      schedcore/

CRYPTOAPI SUBSYSTEM (FIXTURE)
F:      cryptoapi/
"""


@dataclass(frozen=True)
class FixtureInfo:
    repo_dir: Path
    vulns_dir: Path
    cve_shas: List[str]
    cutoff: str


def _run(args, cwd):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                            env={"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                                "HOME": str(cwd), "PATH": "/usr/bin:/bin"})
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace')}")
    return result


def build(root: Path, *, cutoff: str = CUTOFF) -> FixtureInfo:
    root = Path(root)
    repo_dir = root / "repo"
    vulns_dir = root / "vulns"
    repo_dir.mkdir(parents=True)
    vulns_dir.mkdir(parents=True)

    _run(["init", "-q", "-b", "main"], repo_dir)
    _run(["config", "user.email", "fixture@example.invalid"], repo_dir)
    _run(["config", "user.name", "Fixture Author"], repo_dir)

    (repo_dir / "MAINTAINERS").write_text(MAINTAINERS_TEMPLATE)
    _run(["add", "MAINTAINERS"], repo_dir)
    _run(["commit", "-q", "-m", "Add MAINTAINERS"], repo_dir)

    cutoff_date = datetime.fromisoformat(cutoff).replace(tzinfo=timezone.utc)
    # 30 commits strictly before cutoff (1 per day going back), 20 strictly
    # after (1 per day going forward) -> matches N_AFTER_CUTOFF / pool split.
    n_before = N_COMMITS - N_AFTER_CUTOFF
    dates = (
        [cutoff_date - timedelta(days=n_before - i) for i in range(n_before)]
        + [cutoff_date + timedelta(days=i + 1) for i in range(N_AFTER_CUTOFF)]
    )

    cve_shas: List[str] = []
    for i, date in enumerate(dates):
        subsystem = SUBSYSTEMS[i % len(SUBSYSTEMS)]
        subdir = repo_dir / subsystem
        subdir.mkdir(exist_ok=True)
        fname = subdir / f"file_{i}.c"
        is_cve = i % (N_COMMITS // N_CVE_COMMITS) == 0 and len(cve_shas) < N_CVE_COMMITS
        subject = (
            f"{subsystem}: fix use-after-free in file_{i}"
            if is_cve else
            f"{subsystem}: tidy up file_{i}"
        )
        fname.write_text(f"/* fixture change {i} */\nint fixture_{i}(void) {{ return {i}; }}\n")
        _run(["add", str(fname.relative_to(repo_dir))], repo_dir)
        env_date = date.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        _run(["commit", "-q", "-m", subject,
             f"--date={env_date}",
             "--author=Fixture Author <fixture@example.invalid>"], repo_dir)
        # git_kernel.py's extractor reads %aI (author date), which --date=
        # sets directly -- the committer date (defaults to "now") is never
        # read downstream, so it doesn't need forcing.
        sha = _run(["rev-parse", "HEAD"], repo_dir).stdout.decode().strip()
        if is_cve:
            cve_shas.append(sha)

    mapping = {sha: f"CVE-2026-{10000 + idx}" for idx, sha in enumerate(cve_shas)}
    (vulns_dir / "mapping.json").write_text(json.dumps(mapping, indent=2, sort_keys=True))

    return FixtureInfo(repo_dir=repo_dir, vulns_dir=vulns_dir, cve_shas=cve_shas, cutoff=cutoff)
