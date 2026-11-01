# Sprint: deep-research-world-forge

**ID:** 2026-08-06-deep-research-world-forge
**Started:** 2026-08-06
**Product:** arail

## Task

Add a third World Forge source mode that does live internet research (not
just the existing Wikipedia-only "fetch" mode) to curate a World's starting
term base — deep, current, specialized jargon that generic Wikipedia
coverage or an LLM's static "dream" knowledge won't capture well. Concrete
test case: the operator wants to build a "Quantum" World with current
post-quantum cryptography/encryption protocol terminology.

Operator's exact framing on sizing: "Maybe keep it generic in terms of
numbers but effort may dictate the size of the base" — an effort/quality
dial rather than another fixed term-count preset menu.

Origin: follow-up from `sprints/2026-08-06-lab-integrity-review/` (the
World-persistence review). Confirmed via full read + grep of ROADMAP.md and
sprints/BACKLOG.md that this is unscoped, greenfield work — nothing existing
to reconcile with.

## Context handed to the visionary (verify before load-bearing on later phases)

- World Forge today: two source modes, "dream" (LLM, sizes 25/50/100) and
  "fetch" (Wikipedia-only, `src/arail/world_sources/wikipedia.py`, domain-
  allowlisted to wikipedia.org, sizes 25/50/100/250/512). `max_terms` in
  `POST /api/worlds/forge`, `#forge-size` radiogroup in `worlds.html`.
- The Browser agent (`src/arail/agents/browser.py`) already does real web
  research and already prefers arxiv.org/nist.gov/ieee.org-class sources —
  but is completely unwired from World Forge today (zero call sites,
  confirmed by grep across `dac_world/forge.py`, `world_routes.py`,
  `librarian_scout.py`).
- Horizon-watch (`arail.research.agenda_watch`) is NOT a fit — it's a
  staleness-watcher for a World that already exists (diffs already-declared
  `agenda.json` URLs against a stored hash), not a discovery/curation engine
  for a *new* World's initial term base.
- No "effort" abstraction exists anywhere in the codebase — sizes are
  hardcoded presets/clamps, never a compute/time/quality budget.
- Airgap gating: default `LAB_MODE=airgapped` blocks all outbound calls via
  an egress guard. Any internet-research mode needs `LAB_MODE=hybrid` (an
  explicit two-step opt-in UI toggle) AND a new scoped consent gate — the
  existing Wikipedia consent (`allow_bootstrap_fetch`) is domain-allowlisted
  to wikipedia.org specifically and can't be reused as-is for
  arxiv.org/nist.gov/ieee.org. Egress guard has documented gaps (doesn't
  wrap aiohttp, raw sockets, subprocess shell-outs).

## Phases

| Phase | Subagent | Artifact | Status | Started | Finished | Verdict |
|---|---|---|---|---|---|---|
| think | visionary | VISION.md | done | 2026-08-06 | 2026-08-07 | **reject as scoped** |
| plan | architect (design) | ARCHITECTURE.md | blocked | — | — | gated on the three-arm experiment |
| build | builder | BUILD_LOG.md | blocked | — | — | — |
| review | architect (review) | REVIEW.md | blocked | — | — | — |
| test | qa | TEST_REPORT.md | blocked | — | — | — |
| ship | — | PR | blocked | — | — | — |

## Decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-06 | Started at `think`, not `plan` | Genuinely new feature with real strategic tension (adding a live-internet-research surface to a product whose default identity is airgapped/local-first) — not a bug fix with an obvious win condition. |
| 2026-08-07 | **Rejected as scoped**; pipeline blocked before `plan` | See VISION.md. Three premises in the brief below failed verification, and the decisive experiment (three-arm forge coverage test) costs one evening and has not been run. |

## Corrections to the "Context handed to the visionary" section above

Verified on disk 2026-08-07. **The section above contains two errors — do not
build on it without reading VISION.md first.**

1. **WRONG: "Any internet-research mode needs `LAB_MODE=hybrid` … the existing
   Wikipedia consent is domain-allowlisted to wikipedia.org and can't be reused
   as-is."** `egress.allow_bootstrap_fetch` (`src/arail/egress.py:590`) is
   explicitly the "ONE consent-gated exemption that works airgapped" —
   `_check_egress_or_raise` consults `_bootstrap_allows(host)` at step 2b,
   *before* the `is_airgapped()` deny at step 3, and
   `tests/test_egress_bootstrap.py::test_allowlisted_host_passes_and_is_audited`
   asserts it under the `airgapped` fixture. The host allowlist is a **call-site
   parameter** (`wikipedia.py:38` → passed at `:202`), not a hardcode. Adding
   arxiv.org is a tuple change. No hybrid flip, no new gate concept.
2. **INCOMPLETE: the Browser agent as the engine.** `browser.py:110` is
   `subprocess.run(["agent-browser", ...])` — Playwright/Chromium out of
   process. The egress guard patches `requests`/`urllib`/`httpx`, all
   **in-process**. A subprocess is invisible to all three, so host allowlisting
   and `egress.jsonl` auditing become unenforceable, which would make the
   existing forge banner (`worlds.html:92`, "every request is audited") false.
   Plus it hard-depends on an optional Node/npm/Chromium toolchain
   (`scripts/setup.sh:776-785` installs it best-effort and warns on failure).
3. **MISSING: the motivating corpus is unreadable.** NIST FIPS 203/204/205 are
   PDFs. `grep` for `pypdf|pdfminer|fitz|pdftotext` across `src/` and
   `pyproject.toml` returns **nothing**. `pkb.py:38` files `.pdf` as `"papers"`
   but `_PKB_TEXT_SUFFIXES` (`pkb.py:376`) and `librarian_scout._TEXT_SUFFIXES`
   (line 53) both exclude it. PDFs are filed and then never read by anything.
4. **MISSING: a third existing path.** `worlds.html:46-53` already ships a
   "Forge brain" radiogroup with a `☁️ Frontier API` (Claude) option. Current
   PQC terminology via a frontier model is available today, zero code, untried
   on this subject.

## Skipped phases

| Phase | Reason |
|---|---|

## Notes

Product-specific gating from arail's CLAUDE.md to weigh at later phases:
setup-on-clean-machine, Buddy quality, **security (it runs on others'
machines)**, onboarding clarity, failure-mode grace. QA allocation for this
product: 30% setup / 30% Buddy / 20% security / 10% happy / 10% regression —
the security weighting matters more than usual here given the feature adds
a new outbound-network surface.
