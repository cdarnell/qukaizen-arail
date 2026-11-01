# Autoresearch integration — what it actually does, and whether it holds up

> Status: **audit; H0–H4 fixed, H5 + four decisions open.** Opened
> 2026-08-16, last updated 2026-08-17.
> Written to answer three operator questions: does the commit-on-win /
> revert-on-loss loop work for someone over the long term; is this all
> private git workspaces; and how do we have DDaC without our own git
> layer. Every claim below is anchored to a file and line in this repo
> (or in `qukaizen-dac`).
>
> The headline finding is not any of the five hazards this audit set out
> to catalogue. It is **H0**: the loop could not complete a single pass,
> because it ran a plain `git add` on two whitelisted paths that
> `.gitignore` excludes. Every git seam in the suite was stubbed, so no
> test ever ran the real command. Auditing the pile turned up a floor
> that wasn't there.

---

## 1. Two loops, one name

The single largest source of confusion — including in our own README —
is that **two different engines are both called "autoresearch"**. They
share a word and nothing else. Only one of them touches git.

| | **Researcher loop** (`/research`) | **Tuning loop** (`/tuning`) |
|---|---|---|
| Code | `src/arail/agents/researcher.py`, `src/arail/research/mini_experiments.py` | `src/arail/experiments/autoresearch.py`, `experiments/git_ops.py`, `experiments/branch_browser.py` |
| Touches git? | **No.** Grep-confirmed: zero git calls in `agents/` or `research/` | **Yes** — `git_ops.py` is the only write-side git in `src/` |
| Writes to | PKB reports (`lab/pkb/agents/research/…`), `lab/data/experiments/<id>.json` | `autoresearch/<ts>-<slug>` branches **in the ARAIL repo itself**, plus `config/tuning*.yml` and `lab/data/*-bench.jsonl` |
| "Positive" means | archetype verdict `supported` / `not_supported` / `inconclusive` / `cannot_run` (`mini_experiments.py`) | median `decode_tok_per_sec` delta ≥ `improvement_threshold_pct`, default **5 %** (`autoresearch.py:694-696`, `config/tuning.yml:129`) |
| Negative path | writes an honest "not supported" report; nothing to revert | see §2 — **not** `git reset` |
| Gate | none | `ARAIL_AUTORESEARCH_ENABLED` must be set (`autoresearch.py:633-639`) **and** the tree must be clean (`git_ops.py:107-118`) |

The `/research` page has already been de-conflated in the UI (the
branches panel at `research.html:294-322` is tier-gated and explicitly
attributed to the *other* loop, and `tests/test_research_page_dom.py`
enforces that). The remaining conflation is in prose — see §6.

---

## 2. Where the git actually is, precisely

All write-side git lives in `src/arail/experiments/git_ops.py`. It runs
`subprocess.run(["git"] + args, cwd=_repo_root())` — never `shell=True`,
no GitPython anywhere in the repo.

**The repo it operates on is the ARAIL checkout itself** — not a scratch
repo, not a lab subdirectory, not a user-specified target:

```python
def _repo_root() -> Path:                       # git_ops.py:74-76
    return Path(__file__).resolve().parent.parent.parent.parent
```

Operations, exhaustively:

- **Read:** `rev-parse HEAD` / `--short HEAD` / `--abbrev-ref HEAD`,
  `status --porcelain` (`git_ops.py:89-104`).
- **Refuse-if-dirty:** `assert_clean_tree()` (`:107-118`).
- **Branch per variant:** `checkout -b autoresearch/<exp_id> <base>`
  (`:121-131`), where `exp_id = "%Y%m%d-%H%M%S" + "-" + slug(label)`
  (`autoresearch.py:705`). Refuses to clobber an existing branch.
- **Commit on win:** stage each file individually (`# Stage explicitly;
  never git add -A.` `:162`), reject anything outside the whitelist,
  reject an empty diff, then `git commit -F -` with the message on stdin
  (`:145-186`).
- **Link out:** `remote get-url origin`, string-munged into a GitHub URL
  (`:189-208`). Read-only; no fetch.

**The write whitelist is four files** (`git_ops.py:44-49`), enforced
before staging and pinned by `tests/test_experiments.py`:

```
config/tuning.yml   config/tuning-mlx.yml
lab/data/aerollm-bench.jsonl   lab/data/mlx-bench.jsonl
```

**On a losing variant there is no `git reset`.** The actual code:

```python
def abort_experiment(return_to_branch: str) -> None:   # git_ops.py:134-142
    _run(["checkout", "--", "."], check=False)
    _run(["checkout", return_to_branch])
```

That discards working-tree changes and switches back. The losing branch
ref is **deliberately left in place** for human inspection (`:139-140`).
There is no `git reset`, no `git revert`, no `git stash`, no
`branch -D`, no `--force`, and no `git init` anywhere in `src/`,
`scripts/`, or `arailctl`. **Nothing ever pushes** (`autoresearch.py:40`,
"Never pushes. No network ops from this module.").

`branch_browser.py` is strictly read-only and validates every branch
name against `^autoresearch/[A-Za-z0-9._-]+$` before it reaches a
subprocess (`:37`, `:98-104`).

---

## 3. The three questions, answered

### Q1. Is this really working for someone long term?

The mechanism is sound and unusually well-guarded for what it is
(whitelist, clean-tree gate, env flag, no push, no force, explicit
staging). But it has **five long-horizon hazards, none of which is
currently tested or documented**, and all of which only bite after weeks
of use rather than on day one:

**H1 — the baseline commit lands on whatever branch you're on.**
~~`autoresearch.py:661-691` captures the baseline and commits it
*before* any `autoresearch/*` branch is created.~~ **FIXED 2026-08-16.**
The baseline is now committed on its own `autoresearch/baseline-<ts>`
branch, created before anything is staged; variants branch from *that*
rather than from the user's branch; and the loop checks the user's
original branch back out in a `finally`, so a win or a crash no longer
strands them on a branch they didn't pick. The restore is guarded by a
`restore_to` sentinel that stays `None` until we've actually left the
user's branch — critical, because the restore path runs
`git checkout -- .` and must never fire on the dirty-tree abort, where
the uncommitted work is the *user's*. Four regression tests in
`tests/test_experiments.py` pin all of this, including that no commit is
ever ordered before the first branch creation.

**H2 — `arailctl update` will eventually refuse.** `docs/cli.md:103`
documents `git pull --ff-only`. Once a user has any local autoresearch
commit on `main`, that pull stops fast-forwarding and updating the lab
fails with a git error the user has no context for. **Resolved by the
H1 fix** — the loop no longer produces commits reachable from `main`.
Users who already ran the loop on `main` before this fix still have such
commits and will still hit the refusal; no migration is provided, since
the audit found zero `autoresearch/*` branches and no bench files, i.e.
the loop has never actually been run here.

**H3 — branches accumulate forever.** ~~Nothing ever prunes
`autoresearch/*`.~~ **FIXED 2026-08-17** — `./arailctl autoresearch
prune`, dry-run by default. Every gate is a *keep* gate, so a new failure
mode defaults to keeping: wins, the branch you're on, anything classified
`unknown`/`running`, the newest N (default 20), anything younger than the
age gate (default 14d), and anything outside the namespace all survive.
The plan is re-validated at the moment of deletion, so a stale or
hand-edited plan cannot delete a protected branch. A receipt carrying the
full SHA and a ready-to-paste `git branch <name> <sha>` is appended
*before* each delete — the ref goes, the objects stay.

**H4 — bench files grow without bound and are committed.** ~~No
rotation.~~ **FIXED 2026-08-17** — `./arailctl autoresearch rotate`,
also dry-run by default: the newest N records (default 5000) stay in
place, older ones are *appended* to a sibling archive, and the live file
is replaced atomically so a crash cannot truncate it. Nothing is
discarded.

Rotation is deliberately **not** automatic inside the loop: auto-rotating
would silently rewrite a file that sits inside the commit whitelist,
producing diffs mid-pass that the user never asked for. State the cost of
that choice plainly — a lab that runs the loop nightly and never runs the
CLI still grows without bound. Wiring rotation into the pass, or into
`arailctl update`, is a live option that needs a decision about who owns
that write, not more code.

**H0 — the loop could not complete a single pass.** Found 2026-08-17
while building the H4 rotation, and it reframes everything above:
`lab/data/` is gitignored wholesale (`.gitignore:42`), and two of the
four whitelisted paths live under it. `commit_experiment` ran a plain
`git add` on them; git exits 1 on an ignored path and stages nothing, and
`_run` uses `check=True`, so this raised `CalledProcessError` — from the
*baseline* commit, the first commit of every pass, whose caller catches
only `GitSafetyError`. The exception propagated to the outer handler and
the pass ended in `error` before reaching a single variant.

Verified empirically in a scratch repo (`git add` on an ignored path:
exit 1, nothing staged; with `-f`: exit 0, staged), not inferred from
reading. **FIXED** by force-adding, which is safe here precisely because
`-f` can only ever reach `ALLOWED_WRITABLE_FILES` — the membership check
runs first, so forcing cannot smuggle an unlisted path into a commit.

This is why the empty-verification rows below matter. The entire suite
stubbed every git seam — correct for testing loop logic, but it meant no
test ever ran the real `git add`, and the loop's headline behavior was
broken in a way no one would notice until they ran it. There is now a
`tests/test_git_ops_real_repo.py` that drives real git, and it fails with
the original `CalledProcessError` if the `-f` is removed.

**H5 — clean-tree gate vs. a lab someone actually edits.** ARAIL is a
blueprint people fork and modify. `assert_clean_tree()` means any
in-progress local edit blocks the loop entirely, with a message that
tells them to commit or stash their own work to run *our* loop.

A sixth, softer one: `_repo_root()` walks four parents up from
`__file__`. That is correct for a source checkout and for `pip install
-e`, but a non-editable install puts the package in `site-packages`,
where the walk lands outside any git repo and every git call fails.

**Verdict:** it works, and it is honest about what it measures. It is
not yet *durable* — H1/H2 in combination is the one that will actually
break a real user's lab, because it turns "I ran the experiment loop"
into "my updates stopped working" with no visible link between cause and
effect.

### Q2. Is this all private git workspaces?

**No hosted git is involved anywhere, and none is required.** The loop
is a purely local ledger inside whatever ARAIL checkout the user has.
It never pushes, never fetches, never clones, never inits. `diff_url()`
is the only code that even reads a remote, and it only string-munges the
URL to render a link — if there is no `origin`, it returns `None` and
the UI degrades.

So the question "private workspaces or not?" doesn't have a product
answer today because the product doesn't have an opinion: whether the
user's clone is a private fork, a public fork, or has no remote at all
is entirely invisible to the loop. The "workspace" is one `git
rev-parse` away and stops there.

This is defensible for a local-first blueprint, and it is consistent
with `LAB_MODE=airgapped`. What it means, though, is that **there is no
sharing story**: two people running the same lab produce two disjoint,
unmergeable experiment histories, and there is no artifact you could
hand someone that says "here is what my lab learned." If we ever want
that, it is a new design, not a flag — and pushing `autoresearch/*`
branches to a shared remote is the *wrong* shape for it (hundreds of
refs, whitelist-limited diffs, no cross-machine comparability of
tok/s numbers measured on different hardware).

### Q3. How do we have DDaC without our own git layer?

Because **DDaC never used git as its integrity or addressing layer.**
This is the key correction to the framing of the question.

- `qukaizen-dac/dac_world/seal.py` is the entire sealer and imports only
  stdlib (`hashlib, json, os, shutil, datetime, pathlib`). A grep for
  `git` across `qukaizen-dac`'s `src/`, `dac_world/`, and `scripts/`
  returns only the substring inside "logit". **Zero git dependency in
  the pipeline.**
- The seal is `sha256(exact bytes)` per file, over six sealed files,
  with `world_sha` = the hash of `terms.json`. `created_at` is carried
  across reseals (the shipped `lab/worlds/ai/manifest.json` literally
  holds `1970-01-01T00:00:00.000Z`) so wall-clock is not part of
  identity.
- Composition is `(slug, path, sha256)` references, never inlined
  content, with byte-binding recomputed and refused on drift (dac
  `docs/adr/0002-world-factories.md`). That is a Merkle-shaped DAG; git
  would be redundant to it, not foundational.
- Git's only mechanical role in dac is `.githooks/post-commit` firing
  `make build` — a *trigger*, plus git as the transport for
  human-authored source markdown. Compiled artifacts are git-ignored by
  policy.

Where git-shaped semantics *are* needed at runtime, ARAIL already built
purpose-specific substitutes rather than reaching for git:

| Git concept | ARAIL/DDaC equivalent |
|---|---|
| commit (atomic write) | `reseal_bundle` — temp dir + `os.rename` + rollback |
| fsck / verify | `world_mount.verify_seal` — rehash every sealed file |
| history / log | `evolution.json` (`arail.world-evolution/v1`), append-only, seal-exempt but preserved verbatim across reseals via `KNOWN_SIDECARS` |
| content address | `world_sha256` / `corpus_sha256` |
| undo | `git checkout -- lab/worlds/<slug>` — documented *only* as the human recovery path |

So the answer is: we don't need a git layer for knowledge artifacts,
and adding one would duplicate the seal. **The real debt is the
opposite shape** — the one cross-repo artifact that *isn't*
content-addressed is the vendored source copy of `dac_world`
(ARAIL `docs/adr/0004`), and that is exactly the edge that has already
drifted (workspace `INTEGRATION_AUDIT.md` edge **E4**, rated CRITICAL;
the divergence is a changed description string in `seal.py`). ADR-0004's
own preferred long-term fix is a published wheel — *more* content
addressing, still no git layer.

---

## 4. Decisions needed from the operator

These are not implementable without a call, and each one changes the
shape of the fix:

1. ~~**Baseline commit target (H1).**~~ **DECIDED 2026-08-16 — moved
   onto its own `autoresearch/baseline-<ts>` branch.** The
   `autoresearch/*` namespace is now genuinely the only thing the loop
   writes to. Implemented; see H1 above.
2. ~~**Update collision (H2).**~~ **Resolved by (1)** — the loop no
   longer produces commits reachable from `main`, so `git pull
   --ff-only` has nothing to trip over. Still open as a *nicety*:
   whether `arailctl update` should detect and explain pre-existing
   local commits rather than surfacing a raw git error.
3. ~~**Retention (H3/H4).**~~ **DECIDED 2026-08-17 — prune old losers
   and superseded baselines, keep every win forever.** Shipped as
   `./arailctl autoresearch prune|rotate`, dry-run by default, with
   conservative defaults (keep newest 20, min age 14d, 5000 bench lines)
   that are all overridable per invocation. Still open: whether rotation
   should become automatic, and who owns that write.
4. **Researcher ledger.** `sprints/2026-05-11-experiment-branches/SPRINT.md:30`
   deferred "wire the Researcher loop to git" to a follow-up sprint that
   was never created. Is PKB-only the *final* answer for the Researcher
   (recommended — it matches `docs/agent-loop.md`'s act-scoping and the
   DDaC reasoning in Q3), or is that still open?
5. **`lab/data/experiments/` placement.** The clean-experience report's
   Gap 8 — Researcher experiment records live outside the PKB root, so
   "wipe the PKB = wipe memory" is not true for them. Move under
   `lab/pkb/`, or accept and document?
6. **Sharing (Q2).** Is "no sharing story" the intended end state for a
   local-first blueprint, or is a portable experiment-report artifact
   something we want on the roadmap?

---

## 5. Verification protocol

Re-runnable, non-destructive. Observed results as of 2026-08-16:

| Check | Command | Result |
|---|---|---|
| Test baseline | `.venv/bin/python -m pytest tests/test_experiments.py tests/test_branch_browser.py tests/test_autoresearch_e2e_fake_aerollm.py tests/test_mini_experiments.py tests/test_research_page_dom.py -q` | **76 passed.** Note: must be the project venv (Python ≥ 3.10). A bare `python3` on this machine is 3.9, which cannot even *collect* `test_autoresearch_e2e_fake_aerollm.py` (`str \| ModelResponse` needs 3.10) and reports spurious `fastapi` import failures. |
| No reset/push/init | `grep -rn "git reset\|git init\|git push" src/ scripts/ arailctl` | **zero hits** — confirms §2 |
| Branch accumulation | `git branch --list 'autoresearch/*'` | **0** in the operator's checkout — the loop has never been run for real here, so H3/H4 are unobserved-but-structural, not yet realized |
| Bench files | `ls lab/data/*bench*` | absent — same conclusion |
| Baseline branch target | read `autoresearch.py:661-697` | confirmed: commit at `:673` precedes `origin_branch = git_state().branch` at `:697` |

~~The third and fourth rows are the important ones for honesty: this
loop has not been exercised end-to-end.~~ **It has now — 2026-08-18,
first real pass ever, on the MLX lane.** What that one run bought:

- **H1 confirmed in production, not just in tests.** The baseline commit
  landed on `autoresearch/baseline-20260818-065724`; the working branch
  was untouched; the tree came back clean and checked out where it
  started.
- **Two more blockers, both invisible from reading.** The MLX research
  model was named by HF id and absent from the HF cache, so an airgapped
  lab — the default — died at baseline while the checkpoint sat in
  `ARAIL_MODELS_DIR`. And `kv_bits` variants cannot run at all with the
  default `max_kv_size=4096` (`RotatingKVCache Quantization NYI`), which
  is three of eight MLX candidates failing every pass behind the message
  "no measurable tok/s".
- **A real measurement.** Baseline 62.88 tok/s; `prefill-1024` at
  +0.3%, correctly recorded as a loss against the 5% threshold.

The pattern is worth stating plainly, because it held four times in a
row (H0, the keep_alive 400, the model id, the KV knob): **every
remaining defect in this loop was one that only running it could find.**
Reading the code found the hazards; running it found the breakage.

**On the full suite.** `pytest tests/ -q` reports **60 failures out of
5,614** on this branch. None are caused by the H1 fix, established by
direct isolation rather than inference: reverting only
`autoresearch.py` + `test_experiments.py` in the same worktree and
re-running the same 33 affected files yields a **byte-identical failure
set** (39/460 in that subset). Independently, none of the 33 failing
files reference `autoresearch` or `arail.experiments` at all. Treat
those as pre-existing and out of scope for this work.

**They have since been triaged** — see
[the test-suite triage](test-suite-triage.md). The short version: 33 of
54 pass when their file runs alone, so most of that red is cross-test
pollution rather than broken product, and the failures that *look* most
like an API regression (`KeyError: 'optional_backends'`, "Legacy branch
dropped keys") are exactly the misleading ones. Nothing runs the full
suite in CI, which is how it got here.

---

## 6. Doc drift fixed alongside this audit

Prose that promised behavior the code does not have:

- `README.md` — Autoresearch section and the Researcher blurb ("writes
  code, runs experiments, commits the winners") described the Tuning
  loop while pointing at the Researcher. **Fixed.**
- `docs/agent-loop.md` — said `git reset` (three places) where the code
  does checkout-and-leave-branch, and pointed at `experiments/tuning.py`
  (the schema module) instead of `autoresearch.py` / `git_ops.py`.
  **Fixed.**
- `docs/tuning-loop.md` — listed two whitelisted files where the code
  has four; wrong GitHub org in two URLs. **Fixed.**
- `src/arail/portal/templates/research.html` — tagline still carried
  branch language on a page whose engine creates no branches. **Fixed**
  (panel wording untouched; `tests/test_research_page_dom.py` guards it).

---

## 7. Follow-ups filed

Each hazard in §3 Q1 and each decision in §4 is filed in
`sprints/BACKLOG.md` under "Autoresearch durability". None of them is
implemented in this branch — H1 and H2 in particular need decision (1)
before code.
