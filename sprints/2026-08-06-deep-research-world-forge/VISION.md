# Vision: Deep Research World Forge

**Date:** 2026-08-07
**Product:** arail
**Wedge size:** n/a — **rejected as scoped**. The counter-proposal below is one sprint, and is deferred.

## Verdict up front

**REJECT as scoped.** Not because live research in ARAIL is wrong — it isn't,
and the airgap architecture already has the right shape to allow it — but
because three of the proposal's load-bearing premises did not survive contact
with the code, and because the one experiment that would tell us whether the
feature is needed at all has not been run and costs an evening, not a sprint.

Specifically:

1. The premise that this needs `LAB_MODE=hybrid` + a new consent gate is
   **false**. The existing gate already does this, airgapped, today.
2. The premise that the Browser agent is the engine is **fatal**. It is the one
   component in the repo that the egress guard structurally cannot see.
3. The motivating case's best sources are **PDFs that ARAIL cannot read at
   all** — a gap no amount of web research closes.

Details, and what to do instead, below.

---

## User

The operator (Charles), on his own machine, forging a personal "Quantum" World
so that a mounted Buddy/Researcher can hold a competent conversation about
post-quantum cryptography — ML-KEM, ML-DSA, SLH-DSA, hybrid key exchange,
harvest-now-decrypt-later, crypto-agility — terminology that stabilized in
NIST's FIPS 203/204/205 (Aug 2024) and has moved since.

This is a **real recurring persona**, and I want to say so plainly because the
brief invited me to dismiss it as a one-off. It isn't: Debt Finance World,
Video Games World, and now Quantum World are three World-forging attempts in
roughly six weeks. Forging Worlds is what this operator actually does with
ARAIL.

But the recurrence cuts the other way from what the proposal assumes. Per
`~/.claude/.../memory/MEMORY.md`: the Debt Finance World sprint is "design
complete, blocked on 7 operator decisions"; the Video Games World follow-ups
are unresolved; and per `sprints/2026-08-06-lab-integrity-review/BRIEF.md`, the
Debt Finance World is the one whose mount/swap silently zeroed the Compiled-KB
gate for two weeks (554 of 556 approvals dangling, `search_for_agents`
returning nothing for any query in any World, no error anywhere).

**Three Worlds started. Zero landed. One of them broke retrieval for a
fortnight.** The bottleneck in this operator's workflow is demonstrably not
"the starting term base isn't deep enough." It is that Worlds don't survive
contact with the rest of the lab.

## Problem

**The problem as asked:** neither existing forge mode produces a term base with
current, specialized, standards-body jargon. `dream` is bounded by a 1B/7B
local model's static training. `fetch` is bounded by Wikipedia's generic
coverage of a fast-moving field.

**The actual underlying pain, stated honestly:** the operator wants a World
whose terms are *both current and attributable*. Today he can have one or the
other, never both:

| Existing path | Currency | Provenance tier |
|---|---|---|
| `dream` + local brain | poor (1B/7B static weights) | `model-asserted` |
| `dream` + **frontier brain (Claude)** | **good** | `model-asserted` |
| `fetch` (Wikipedia) | fair | **`sourced`** |
| *proposed deep research* | good | `sourced` |

That fourth row is a genuine, non-imaginary gap. I am not going to pretend
otherwise. The narrow thing this feature would uniquely provide is
**`sourced`-tier provenance from authoritative non-Wikipedia domains.**

The problem is that we have no evidence the operator needs that fourth row,
because **rows two and three have never been tried on this subject.** The
`worlds.html` forge panel already ships a "Forge brain" radiogroup with a
`☁️ Frontier API` option ("Claude via your saved key · richer drafts") that
routes the whole forge through a frontier model. That is a one-click,
zero-code path to current PQC terminology that nobody has run against
"post-quantum cryptography." We are being asked to build a third source mode
before the second source mode and the existing brain toggle have been tested
against the actual motivating subject.

### Three premises that did not survive verification

I want these on the record because later phases would have built on them.

**(1) "Needs `LAB_MODE=hybrid` + a new scoped consent gate." — False.**

`egress.allow_bootstrap_fetch` (`src/arail/egress.py:590`) is documented as
"the ONE consent-gated exemption that works airgapped," and
`_check_egress_or_raise` consults `_bootstrap_allows(host)` at step 2b —
*before* the `is_airgapped()` deny at step 3. The test suite asserts this
directly: `tests/test_egress_bootstrap.py::test_allowlisted_host_passes_and_is_audited`
takes the `airgapped` fixture. Wikipedia forge mode does not require hybrid and
neither would arXiv.

The host allowlist is a **parameter**, not a hardcode. `wikipedia.py:38` is
`BOOTSTRAP_HOSTS = ["wikipedia.org", "wikimedia.org"]`, passed as an argument
at line 202-204. Extending consent to `arxiv.org` is, at the gate layer,
changing a tuple. The gating story is *far cheaper* than the brief assumed —
which makes it all the more important that we don't spend the savings on the
wrong engine.

**(2) "The Browser agent is the engine." — Fatal, and it's the crux.**

`src/arail/agents/browser.py:110` runs `subprocess.run(["agent-browser", ...])`
— an external npm CLI driving Playwright/Chromium. The egress guard patches
`requests.adapters.HTTPAdapter`, `urllib.request`, and `httpx` transports. All
three are **in-process**. A subprocess is invisible to every one of them.

Route the forge through the Browser agent and all of the following become
untrue simultaneously:

- **Host allowlisting cannot be enforced.** Chromium follows redirects and
  loads subresources — CDNs, fonts, analytics, trackers. `_bootstrap_allows()`
  never sees them.
- **`lab/data/egress.jsonl` stops being a record of what left the machine.**
  The forge banner in `worlds.html:92` currently promises the operator:
  *"every request is audited (lab/data/egress.jsonl). The lab returns to
  airgapped the instant the fetch finishes."* Ship browser-based forge and
  that sentence becomes a false statement in the UI of a product whose entire
  differentiator is truth-in-UI.
- **`allow_bootstrap_fetch` semantics cannot apply at all**, so we would be
  forced to build the second, weaker consent gate the brief anticipated — not
  because the airgap needs it, but because the browser defeats the good one.
- **Clean-machine setup regresses.** `scripts/setup.sh:776-785` installs
  `agent-browser` on a best-effort, prompt-gated basis and warns on failure;
  `docs/PRIVACY.md:144` explicitly says to skip it when Node isn't on PATH. A
  core forge mode would hard-depend on an optional Node toolchain plus a
  ~150MB Chromium download. `setup-on-clean-machine` is 30% of this product's
  QA allocation.

To be precise about where the tension actually lives, since the brief asked:
**the mode is not in tension with ARAIL's identity; the vehicle is.**
`allow_bootstrap_fetch` proves ARAIL's airgap is a *consent architecture, not
an abstinence pledge* — user-initiated, host-allowlisted, contextvar-scoped,
audited. A research forge that uses `requests` against `arxiv.org` inside that
same scope is a natural, bounded extension and needs no new concept. A headless
Chromium subprocess is not an extension of that pattern; it is a hole punched
through the load-bearing wall the pattern rests on, papered over with a banner
that would no longer be true.

**(3) "The Quantum case is served by web research." — Mostly false.**

The authoritative sources for current PQC terminology are NIST FIPS 203
(ML-KEM), 204 (ML-DSA), and 205 (SLH-DSA). They are **PDFs** on
`nvlpubs.nist.gov`. ARAIL has **zero** PDF text extraction: `grep` for
`pypdf|PyPDF|pdfminer|fitz|pdftotext` across `src/` and `pyproject.toml`
returns nothing. `pkb.py:38` maps `.pdf` → `"papers"` for filing, but
`_PKB_TEXT_SUFFIXES` (`pkb.py:376`) is `.md/.txt/.rst/.csv/.json/.html` and
`librarian_scout._TEXT_SUFFIXES` (line 53) is narrower still —
`.md/.txt/.markdown`. **A PDF dropped into ARAIL today is filed and then never
read by anything.**

A headless browser pointed at a NIST PDF gets the Chromium PDF viewer, not
text. IEEE requires a subscription API key. So of the three "standards body"
sources in the proposal, arXiv (Atom API, no key, works with plain `requests`)
is the only one a web-research mode actually reaches — and arXiv is the one
source whose content is *preprints*, i.e. the least authoritative for
*standardized* terminology.

The operator's real motivating corpus is three PDFs he could download in ninety
seconds, and the reason ARAIL can't use them has nothing to do with the
internet.

## Win condition

I cannot write a win condition for the feature as scoped, because the
prerequisite measurement doesn't exist. So the win condition below is for the
**decision**, not the feature — and it is deliberately falsifiable in one
evening.

**Pre-register, before forging anything**, a list of ~20 PQC terms the operator
would consider table stakes for a competent Quantum World. Write it to
`sprints/2026-08-06-deep-research-world-forge/pqc-terms.md` and commit it
*first*. Suggested seed: ML-KEM, ML-DSA, SLH-DSA, FIPS 203, FIPS 204, FIPS 205,
CRYSTALS-Kyber, CRYSTALS-Dilithium, SPHINCS+, Falcon, HQC, hybrid key exchange,
X25519MLKEM768, harvest-now-decrypt-later, crypto-agility, lattice-based
cryptography, code-based cryptography, isogeny-based cryptography, SIKE break,
NIST PQC Round 4.

Then forge "Post-quantum cryptography" three ways and score coverage:

| Arm | Config | Cost |
|---|---|---|
| A | `dream` · local brain · 100 | ~15 min, free |
| B | `dream` · **frontier brain (Claude)** · 100 | ~15 min, a few dollars |
| C | `fetch` · Wikipedia · 250 | ~3 min, free |

Pre-committed decision thresholds:

- **Arm B ≥ 70%** → the feature is **dead**. The existing brain toggle already
  solves the operator's problem and the gap is a discoverability/UX issue, not
  a missing forge mode. Close this sprint; consider a copy change that tells
  users the frontier brain is the answer for fast-moving fields.
- **Arm B or C ≥ 70% but the operator says the `model-asserted` label makes
  the World unusable to him** → the real requirement is *provenance*, not
  *currency*. That is a much narrower feature and a different design. Revisit
  with that as the stated problem.
- **All three arms < 40%** → the gap is real and quantified. Proceed, but to
  the counter-proposal below, not to a browser-driven forge.
- **Anything in between** → defer. An ambiguous result on a feature this
  expensive is a no.

## Wedge

The wedge for the **decision** is the three-arm experiment above: zero lines of
code, one evening, and it is genuinely capable of killing the feature.

If and only if the experiment justifies proceeding, the wedge for the
**feature** is *not* a Deep Research forge mode. It is:

> **PDF text extraction into the existing PKB → librarian-scout pipeline.**

Add a PDF-to-text step at ingest (`pypdf`, pure-Python, no system deps, no
network), extend `_PKB_TEXT_SUFFIXES` and `librarian_scout._TEXT_SUFFIXES` to
cover the extracted text, and let the machinery that already exists do its job:
`mine_candidates()` already scans `pkb/inbox` and `pkb/sources` for capitalized
multi-word phrases and standalone acronyms — which is *precisely* the shape of
`ML-KEM`, `SLH-DSA`, `Module-Lattice-Based Key-Encapsulation Mechanism` — and
already routes them through evidence accumulation, the ubiquity threshold, and
the Compiled-KB approval gate.

Why this is the better wedge on every axis this product cares about:

- **Zero new egress surface.** The operator downloads three PDFs himself. There
  is no consent gate to design, no allowlist to widen, no audit story to
  weaken, no banner that becomes a lie. It works in `airgapped` with the
  default settings, which is the friction profile ARAIL is built for.
- **Zero new dependencies beyond one pure-Python wheel.** No Node, no npm, no
  Chromium, no `agent-browser`. Clean-machine setup is untouched.
- **It fixes a defect, not just a feature gap.** "Drop a PDF in the Knowledge
  tab and nothing whatsoever happens to it" is arguably already broken
  behavior. `/knowledge` has a drag-drop zone and a folder-reveal button for
  `lab/pkb/inbox`; `pkb.py` files PDFs under `"papers"`. The user-visible
  contract implies PDFs are *used*. They are not.
- **It generalizes past Quantum.** Every future World the operator forges from
  a specialized corpus — a standards body, a textbook, a set of papers — is
  served. A NIST-and-arXiv-flavored forge mode serves one World.
- **Terms arrive `sourced`, with a real citation** (file + page), which is the
  fourth-row property the operator actually wants, obtained without touching
  the network.

That is one sprint, and it is a smaller sprint than the one proposed.

## Disconfirming evidence

Pre-committed, in order of when each fires:

1. **Arm B of the experiment scores ≥ 70%.** Feature is dead — the frontier
   brain already ships and already works. This is the single most likely
   outcome and it is why the experiment must run before any code.
2. **The operator declines to run the experiment.** If the three-arm test isn't
   worth one evening, the Quantum World isn't worth a sprint. Shelve.
3. **If the PDF wedge ships and the operator does not ingest a second PDF-based
   corpus within 30 days**, the recurring-need hypothesis is falsified — it was
   a one-off, and we stop investing in the World-bootstrap funnel entirely.
4. **If the PDF wedge ships and the scout's mined terms need more than ~30%
   manual correction at the Compiled-KB gate**, phrase-regex mining is too
   crude for technical corpora and the design is wrong; do not escalate to a
   browser, reconsider the whole approach.

## Displacement

This one is not close, and it is the second independent reason for the
rejection.

`sprints/2026-08-06-lab-integrity-review/BRIEF.md` — filed one day before this
one, from the same operator, in the same subsystem — opens with:

> *"Unfortunately ARAIL has been a crash fest and people are getting
> frustrated. Allow Worlds to load and be persistent."*

Its evidence section documents **five defects in a single week, every one found
by the operator hitting it in normal use rather than by CI**: a 27-hour /
~3,570-restart crash loop from a missing `dac_world` import, an invisible World
picker on macOS, the Compiled-KB gate silently zeroing after a World switch,
plus experiment-tracker and dashboard crashes. Its own diagnosis is that ARAIL
has no regression net around World-switching, so every piece of cross-cutting
state independently discovers this the hard way.

Saying yes to Deep Research World Forge displaces that sprint — and it does so
in **the same subsystem**, adding a new outbound-network surface, a new consent
gate, a new optional-subprocess dependency, and a fourth forge configuration
axis (source × brain × size × effort) to the exact machinery that is currently
losing users to crashes. It also displaces the unresolved Debt Finance World
decisions and the Video Games World follow-ups already sitting in memory.

There is no version of "nothing gets displaced" here. The product is on fire in
this precise area. **Worlds don't persist reliably; building a more elaborate
way to create them is the wrong end of the funnel.**

### On "effort dictates the size of the base"

Interrogated separately, because the brief asked and because the answer is a
clean no as currently framed.

No effort/compute/time-budget abstraction exists anywhere in the codebase.
`max_terms` is a hard clamp — `max(8, min(150, int(self.max_terms)))` in
`ForgeParams.normalized()`, `MAX_TERMS_CAP = 512` in `wikipedia.py` — and it
*derives* `n_categories` and `n_seeds` from fixed thresholds. `ALLOWED_SIZES`,
`FETCH_SIZES`, `ETA_MINUTES`, and `FETCH_ETA_MINUTES` in `world_routes.py:51-55`
are all static lookup tables.

For "effort" to be real rather than cosmetic it must be one of:
(a) a wall-clock budget, (b) a count of source documents consulted, or (c) a
citation-following depth. Only (b) and (c) are honestly "effort," and both are
properties of a research pipeline that does not exist. Shipping (a) — or worse,
relabeling the existing 25/50/100 presets as "Quick / Standard / Deep" — puts a
fuzzy word in front of the same three integers, in the UI of the one product
whose differentiator is honest labeling (`model-asserted` / `sourced` /
`mixed`, the provenance tier, the honesty banners). That is a real and
disproportionate brand cost for a cosmetic change.

There *is* an honest version, and it's the inverse of what was asked: **you
don't pick the size — the sources do.** Point it at N documents and you get
however many terms are genuinely attested in them, which might be 18 or might
be 300. That is a real dial, it is honest, and — note — it is a *source-count*
dial that falls out of the PDF wedge for free. It is not an effort dial, and we
should stop calling it one.

## Recommended next step

**Reject as scoped. Do not proceed to `/architect`.** Concretely:

1. **Run the three-arm experiment.** Commit the pre-registered term list
   *before* forging. One evening, no code. This is the entire ask.
2. **Ship `sprints/2026-08-06-lab-integrity-review/` first, regardless of the
   experiment's outcome.** Worlds that don't persist are a strictly higher
   priority than Worlds that start smarter.
3. **File "PDF text extraction → PKB → librarian scout" to
   `sprints/BACKLOG.md`** as the successor candidate, with a note that it
   supersedes this proposal and that the Browser agent is explicitly *not* the
   vehicle. Also worth filing separately, because it is true independent of
   this feature: *PDFs dropped into the Knowledge tab are filed and never read
   — the surface implies otherwise.*
4. **Revisit date: 2026-09-08**, or immediately upon the experiment showing all
   three arms under 40%.

If the operator overrides this and wants live research anyway, the one
non-negotiable constraint for the architect: **the fetch path must go through
`requests` inside `allow_bootstrap_fetch` with an explicit host allowlist — not
through `agent-browser`.** arXiv's Atom API (`export.arxiv.org/api/query`, no
key, 1 req/3s) satisfies that. Nothing that shells out to a subprocess does,
and shipping one would make an existing user-facing promise false.
