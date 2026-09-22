# Sprint backlog — named revisits

Items filed here are deliberate deferrals from a completed sprint: known,
scoped, not forgotten. Each entry names the sprint that found the gap and
the tech-debt tradeoff that justified deferring it. Pull from this list
when scoping the next sprint in the relevant area — don't let it become a
second, competing TODO list.

---

## Unify blueprint instances with runtime instances (+ decide `ARAIL_HOME`)

**Filed by:** `sprints/2026-07-28-concurrent-worlds/` (WP8), per
ARCHITECTURE.md §12 "Tech debt assessment — Added #1".

**The gap.** This sprint introduced `lab/instances/` as the real,
gitignored runtime home for concurrently-running World instances
(registry, per-instance data/pkb/env-pack — see
`docs/concurrent-worlds.md`). The repo also already has a repo-root
`instances/` directory that `./arailctl blueprint create` scaffolds
(`instances/<name>/{.env,lab.conf,log/,blueprint.toml}`) — config-only,
never itself instantiated into a running process, and nothing under
`src/arail/` reads it. Two directories both named "instances", meaning
different things, is a real future-confusion cost — flagged explicitly
in `docs/concurrent-worlds.md`'s naming note so it isn't silently
stumbled into.

**Why it wasn't done now.** VISION.md scoped this sprint against
unifying with `blueprint create`'s namespace or the
`docs/REPOSITORY_LAYOUT.md:34-78` `ARAIL_HOME` proposal explicitly — both
are separate, load-bearing design questions (does a "blueprint instance"
become a runtime instance? does `ARAIL_HOME` replace `REPO_ROOT`-relative
`lab/` entirely, and if so what happens to every existing path resolver
in `config.py`/`reset.sh`/`scripts/lib/instances.sh`?) that deserve their
own VISION pass, not a rider on this one.

**What a future sprint needs to decide:**
1. Does `blueprint create` start producing REAL runtime instances (i.e.
   converge on `lab/instances/`), or stay a pure scaffolding/config tool
   with a renamed directory to stop the collision?
2. Does `ARAIL_HOME` (an env var naming where "the lab" lives,
   independent of the checkout) get adopted, and if so, does
   `lab/instances/<slug>/` move under it too, or stay checkout-relative
   the way this sprint left it?
3. Whatever is decided, `scripts/lib/instances.sh`'s `inst_root_dir()` is
   the single choke point that would need to change — by design, nothing
   else re-derives that path.

**Mitigation until this is scheduled:** the naming distinction is
documented in `docs/concurrent-worlds.md`'s "Naming note" section and in
`scripts/blueprint.sh`'s own header comment.

---

## `./arailctl reset` should be instance-aware

**Filed by:** `sprints/2026-07-28-concurrent-worlds/REVIEW.md` finding
M6 (architect-review pass).

**The gap.** `reset pkb`, `reset data`, `reset env`, and `reset full` all
operate on the ROOT lab's `config.py`-resolved paths (`lab/pkb/`,
`lab/data/`, `.env`/`lab.conf`). `lab/instances/<slug>/{pkb,data}/` —
which holds a World instance's own knowledge base, chat memory, LanceDB
index, and `secrets.env` — is untouched by every one of them. CLAUDE.md
states the privacy contract flatly ("wipe the PKB = wipe memory");
that contract is not yet true for a World instance. Minimum mitigation
shipped this sprint: documented loudly in `docs/concurrent-worlds.md`
and `CHANGELOG.md`, with a manual `rm -rf lab/instances/<slug>` workaround
named. No code change to `reset.sh`'s destructive paths — REVIEW.md M6
ruled that in scope for documentation only, not a redesign, this pass.

**What a future sprint needs to decide:**
1. Does `reset pkb`/`reset data`/`reset env` grow a `--world <slug>` flag
   that targets one instance's tree instead of (or in addition to) the
   root lab's?
2. Should `reset full`/`reset pkb` at minimum REFUSE and list the
   untouched instance roots when any exist, rather than silently
   completing as if the whole lab were wiped?
3. Whatever is decided must not let a reset command delete instance data
   for a WORLD THAT IS CURRENTLY RUNNING out from under a live process —
   the same "verify before touching" discipline `stop_instance()` already
   applies to killing PIDs should extend to any future data-deleting path.

**Mitigation until this is scheduled:** documented in
`docs/concurrent-worlds.md`'s "`./arailctl reset` does NOT touch instance
data — yet" section, with the manual two-command workaround.

---

## Canonicalize the mount record's `bundle_dir` on adopted Worlds

**Filed by:** `sprints/2026-07-28-worlds-select-removal/REVIEW.md` ASK-1.

**The gap.** `mount()` records the SOURCE path a World was mounted/imported
from (`world_mount.py`), then `_adopt_into_catalog()` copies it into
`WORLDS_DIR/<slug>`. For an externally-imported World those are two
different strings for the same World, so the `in_place_switch_removed`
guard's `cur.bundle_dir != str(bundle_dir)` comparison alone would wrongly
refuse that World re-binding to itself via its catalog slug. This sprint
shipped the narrow fix: the guard also allows when `cur.world ==
target_slug`. That is correct but is a second, string-slug-based notion of
"same World" living alongside the path-based one — a future path/slug
divergence (two dirs sharing a slug, ASK-1's own F7 fixture pattern) could
reintroduce an asymmetry.

**The real fix (not done this sprint):** have `mount()` write the ADOPTED
catalog dir into the mount record when `_adopt_into_catalog()` succeeds, so
one World has exactly one canonical `bundle_dir` regardless of which door
(select/import/import-zip) it arrived through. Touches mount-record
semantics — deserves its own sprint, not a rider fix.

---

## Extract `_refuse_in_place_switch()` — third guard-body copy

**Filed by:** `sprints/2026-07-28-worlds-select-removal/REVIEW.md` INFO.

**The gap.** The `in_place_switch_removed` refusal body is now duplicated
three times (`api_worlds_select`, `api_worlds_import`,
`api_worlds_import_zip`) — ARCHITECTURE.md accepted two copies and named
three as the threshold for extracting a shared helper
(`_refuse_in_place_switch(cur, target_slug) -> Optional[JSONResponse]`).
Not done this pass to keep the review-fix commits minimal and reviewable;
worth doing the next time any of these three endpoints is touched.

---

## De-duplicate `showLaunchCommand()` (welcome.html / worlds.js)

**Filed by:** `sprints/2026-07-28-worlds-select-removal/BUILD_LOG.md` /
`REVIEW.md` INFO.

**The gap.** `welcome.html` and `src/arail/portal/static/js/worlds.js` each
carry their own copy of `showLaunchCommand(slug)` (copy the `./arailctl
start --world <slug>` command to the clipboard + alert). The two templates
don't currently share a JS module, so extracting a common helper means
introducing that sharing mechanism first — out of scope for a UI-removal
sprint. Worth doing if a third copy appears (`nav.js`'s `reveal()` posture
is close but not identical).

---

## `status.sh`'s deliberate `set -e` omission

**Filed by:** `sprints/2026-07-29-elite-cli/REVIEW.md` m4 / §8 unanticipated
debt #2 / re-review §R6.1 item 1 (required action #9).

**The gap.** `scripts/status.sh` runs under `set -uo pipefail` — no `-e` —
unlike every other `scripts/*.sh` in this repo, which all run under
`set -euo pipefail`. This is deliberate: the probe helpers this file calls
(`scripts/lib/services.sh`'s `svc_listening`/`svc_http_status`/etc., and
this file's own instance-record readers) use a nonzero return as DATA
("down"/"unreadable"/"unknown" are legitimate outcomes on the way to
building the status document), not a failure that should abort the
collector. Running the whole file under `-e` would turn every one of those
expected-degraded states into an immediate, undiagnosed exit — the same
class of bug F20 already names for `while read` loops. Documented as a
20-line LANDMINE note in the file header (`status.sh:12`) during the
review-fix pass, but the file's error-handling posture itself was not
changed.

**Why it wasn't done now.** The narrower alternative — scope `set +e`/
`set -e` tightly around just the probe-calling block, rather than omitting
`-e` for the whole 782-line file — was considered during the review-fix
pass and rejected as out of scope: no numbered test currently pins where
that boundary should sit, and retrofitting it means auditing every one of
this file's ~15 probe call sites for what CURRENTLY relies on `-e` being
off globally (a materially larger, riskier change than a documentation
fix).

**What a future sprint needs to decide:**
1. Is the `set +e`/`set -e` scoped-block form worth building, or does the
   file-level `set -uo pipefail` posture stay permanent (matching the
   comment's own "next maintainer" framing)?
2. If scoped, what is the exact boundary — one block per probe, or one
   block wrapping the whole collector phase?
3. A numbered test (`status --json` under a probe helper forced to return
   an unexpected nonzero) should pin whichever boundary is chosen, so this
   doesn't regress silently a second time.

**Mitigation until this is scheduled:** the landmine comment at
`status.sh:12` explains the omission and the road not taken, so the next
maintainer does not have to rediscover the reasoning from scratch.

---

## `install`'s preflight silently mutates the registry

**Filed by:** `sprints/2026-07-29-elite-cli/REVIEW.md` §8 unanticipated debt
#4 / re-review §R6.1 item 2 (required action #9).

**The gap.** `install.sh`'s live-lab preflight (F21/F22) shells out to
`scripts/status.sh --json=full --no-probe` to check whether anything is
running before allowing a mutating phase (`--rebuild-venv`, `deps`). As a
side effect of that read, `status.sh`'s collector calls
`inst_list_slugs`/`inst_prune_all`, which deletes any registry record whose
liveness predicate fails — so a preflight an operator would reasonably
expect to be read-only actually mutates `lab/instances/registry.d/` on
every `install` invocation.

**Why it wasn't done now.** The mutation is correct-ish on its own terms
(`inst_prune`'s documented contract is "remove a record iff it is
provably stale" — never live data, never a data directory, ARCHITECTURE.md
§2.5) and reusing WP5's status collector rather than growing a fourth
liveness check was itself an explicit architecture instruction
(§17: "use WP5's status collector for the liveness preflight — do NOT
grow a new liveness check"). Making the preflight genuinely read-only would
mean either a `status.sh --no-prune` flag (a new flag on a protected file,
with its own test surface) or duplicating the liveness read without the
prune — both larger than a review-fix-pass rider.

**What a future sprint needs to decide:**
1. Is a silent prune during an unrelated verb's preflight actually
   surprising enough to fix, or is "stale records get cleaned up
   opportunistically" an acceptable, even desirable, side effect worth
   documenting explicitly instead (in `docs/cli.md`'s `install` section)?
2. If it should be read-only, does `status.sh` gain a `--no-prune` flag
   that `install`'s preflight passes, or does `install.sh` grow its own
   narrower liveness check (re-opening the "one liveness check" mandate
   this sprint's architecture explicitly closed)?

**Mitigation until this is scheduled:** the mutation is confined to
pruning genuinely-dead records (never live data), and is the same prune
`status`'s own bare invocation already performs on every call — `install`
does not introduce a NEW kind of mutation, just an additional trigger for
an existing, narrowly-scoped one.

---

## `services.sh`/`setup.sh`: hard dependency or a real degrade

**Filed by:** `sprints/2026-07-29-elite-cli/REVIEW.md` n6 / re-review §R6.1
item 3 (required action #9, accepted "on condition it is filed").

**The gap.** `scripts/start.sh`'s root path calls
`inst_load_port_helpers` (which extracts `_port_in_use` from
`scripts/setup.sh` via `awk`) under `set -e` with no guard — if
`scripts/setup.sh` is absent (a copied-out fixture, or a corrupted
checkout), the root path aborts with no message rather than degrading.
Separately, `scripts/lib/services.sh`'s `[[ -f ]]`-guarded `source` (used
by `start.sh`, `status.sh`, and `arailctl`) does NOT crash when
`services.sh` is missing (F4 holds — confirmed by this sprint's own driver
extension, `tests/shell_source_safety_driver.sh` cases #7/#8), but the
degrade it produces is not USEFUL: every root start still fails, just with
a worse message ("✗ Portal did not come up" instead of a named "readiness
probes unavailable" warning).

**Why it wasn't done now.** This is a genuine hard-dependency-vs-honest-
degrade DESIGN decision — not a fix-if-trivial line change. Two real
options exist (make the dependency hard and say so plainly in an error
message, or make the degrade real by having the root path continue with
readiness detection disabled and a loud warning), and picking between them
changes user-facing failure behavior for a case (a `setup.sh`/`services.sh`
missing from an otherwise-checked-out repo) that has no existing numbered
test either way.

**What a future sprint needs to decide:**
1. Is `scripts/setup.sh` / `scripts/lib/services.sh` presence a hard
   precondition for `start`/`status` to run at all (in which case both
   should fail loudly and immediately, naming the missing file), or should
   the root path degrade to "readiness detection unavailable" and continue?
2. Whichever is chosen needs a numbered test (a fresh-checkout-with-a-file-
   deleted scenario) so the choice is pinned, not just documented in
   prose.

**Mitigation until this is scheduled:** none beyond the current (silent
but non-corrupting) failure modes — a missing `setup.sh`/`services.sh` is
an unusual-enough checkout state that it has not been reported in
practice.

---

## `install --json`'s early-exit paths emit no JSON

**Filed by:** `sprints/2026-07-29-elite-cli/REVIEW.md` n4 / re-review §R6.1
item 4 (required action #9).

**The gap.** `status --json` follows F18's doctrine: the document is
ALWAYS emitted, even on a collector failure (an unreadable
`registry.d`, a corrupt record) — errors land in `warnings[]` /
`verdict.code`, never as a bare non-JSON error line. `install --json` does
not follow the same doctrine: its three early exits (unprovisioned lab,
lab live without `--allow-running`, a bad flag) print a plain human error
line to stderr and exit, with no JSON on stdout at all. A caller piping
`install --json | jq` through one of those three paths gets a `jq` parse
error instead of a machine-readable verdict.

**Why it wasn't done now.** This is a documented scope trim, not an
oversight: §5.1 names `arail.install/v1`'s schema (`{"schema", "check",
"verdict": {"code", "state"}}`) but no numbered test (T24-T28, F5-F7,
F21/F22, F28, F32) requires early-exit JSON, and `install`'s own stderr
narration already carries the same information for an operator running
`--json` interactively (§14.1: "no human decoration on stdout" moves it to
stderr, it doesn't delete it). Adding early-exit JSON emission is a small
but real behavior change across three distinct refusal paths, each needing
its own test.

**What a future sprint needs to decide:**
1. Should all three early exits gain a minimal `arail.install/v1` JSON
   emission (`verdict.code` + a `reason` field), matching `status --json`'s
   F18 doctrine?
2. If so, does the schema need a version bump, or is a new optional
   `reason` field additive enough to stay `v1`?

**Mitigation until this is scheduled:** the three early-exit paths are
narrow and well-named on stderr; a caller that checks the exit code before
parsing stdout (the documented, correct usage) is unaffected.

---

## `ARAIL_TIER0_BOOT_WARM`'s export blast radius

**Filed by:** `sprints/2026-07-29-elite-cli/REVIEW.md` n7 (accepted as-is).

**The gap.** `scripts/start.sh:801` and `:1050`'s `export
ARAIL_TIER0_BOOT_WARM=1` (set when `--warm` is passed to `start`/`restart`)
leaks into every subsequently spawned child process on that path — the
memory service, ttyd, jupyter, code-server — not just the portal process
that actually reads it.

**Why it wasn't done now.** Harmless today: nothing besides the portal's
own `_warm_primary_router()` reads `ARAIL_TIER0_BOOT_WARM`, so the leak has
no observable effect. Fixing it means replacing the blanket `export` with
an inline per-invocation env prefix on the ONE `uvicorn` spawn line that
needs it — a small change in isolation, but one that touches a spawn call
site shared with several other env vars, for a purely cosmetic tightening
with no bug behind it.

**What a future sprint needs to decide:** worth doing opportunistically the
next time `start.sh`'s spawn block is touched for an unrelated reason; not
worth a dedicated pass on its own.

**Mitigation until this is scheduled:** the `export` choice and its blast
radius are explained in an inline comment at both call sites.

---

## B2's residual: `stop --root` during a same-port World's boot window

**Filed by:** `sprints/2026-07-29-elite-cli/REVIEW.md` re-review §R6.3
(required action #9).

**The gap.** The review-fix pass's B2 fix
(`scripts/reset.sh`'s `stop_services()`) excludes a live World instance's
portal/memory pids from the QA-17 fallback match by consulting
`lab/instances/registry.d/`'s WRITTEN records
(`inst_list_slugs`/`inst_alive`/`inst_read_record`). But `_instance_start`
spawns the portal at stage `[6/8]` (`start.sh:807`) and does not WRITE the
registry record until stage `[8/8]` (`start.sh:948`) — write-after-ready is
a protected invariant from the Concurrent-Worlds sprint and must not
change. Consequence: a `stop --root`/`restart --root` fired while another
World is mid-boot, on the SAME port as the root lab's configured
`PORTAL_PORT`, can still take it via the fallback — the exclusion set
simply doesn't know about an instance that hasn't finished registering
yet.

**Why it wasn't done now.** Much narrower than the shipped B2 bug (which
fired unconditionally on any same-port collision, live or not): this
residual needs a same-port collision AND a concurrent boot, a much smaller
window. The cheap close the re-reviewer named — `stop_services` refusing
the fallback while any FRESH `.claim` file exists
(`lab/instances/registry.d/<slug>.claim`, written with the launcher pid at
`start.sh:658`, i.e. before the spawn, so it IS available during the
`[6/8]`→`[8/8]` window) — is real, but touches code adjacent to the
protected write-ordering invariant and was explicitly filed as a follow-up
by the reviewer, not implemented in the same review-fix pass that found it
(deliberately — it deserves its own review cycle, not a rider commit).

**What a future sprint needs to decide:**
1. Confirm the `.claim`-file read in `stop_services` cannot itself
   introduce a new hazard (e.g. a stale/orphaned claim file from a crashed
   boot blocking a legitimate `stop --root` indefinitely — needs a TTL or
   a liveness check on the claim's own launcher pid).
2. Add a numbered test that reproduces this exact residual (a live claim,
   no registry record yet, same-port collision) failing before the fix and
   passing after.

**Mitigation until this is scheduled:** the window is narrow (a same-port
collision during the ~1-2 second `[6/8]`→`[8/8]` boot span) and requires an
operator to have already chosen to run a World on the root lab's own
configured port — an unusual, non-default configuration.

**Addendum (QA pass, `sprints/2026-07-29-elite-cli/TEST_REPORT.md` §8):**
the residual has a SECOND, non-timing route to the same outcome. The
exclusion set `stop_services` builds is populated only from **readable**
live registry records (`inst_read_record` succeeding). A **corrupt**
registry record — the same "unreadable/truncated JSON" shape `status
--json` already has to tolerate elsewhere in this sprint (QA-4) — makes
`inst_read_record` fail for that slug, so the record's pids are silently
absent from the exclusion set even though the instance itself may be
genuinely alive and matched by the fallback pattern. No concurrent boot or
same-port timing is required to hit this route — a corrupt-on-disk record
is sufficient by itself. Same narrow blast radius as the boot-window
residual above (still requires the fallback's other preconditions: a
same-port collision and no `--app-dir` in the process's argv), and the
same `.claim`-file-based remedy proposed above would not close this second
route on its own — a corrupt *record* is a different failure shape than a
*missing* one, and the fix would need to treat "registry entry exists but
is unreadable" as itself grounds to withhold the fallback (fail closed)
rather than silently proceeding as if no instance were there at all. Filed
here rather than as a new entry since it shares the same root component,
the same fallback mechanism, and the same recommended review-cycle
treatment as the timing residual above.

---

## `test_reset_stop_scope.py`'s pre-existing failure leaves B2 unit-untested

**Filed by:** `sprints/2026-07-29-elite-cli/REVIEW.md` re-review §R6.4
(required action #9).

**The gap.** `tests/test_reset_stop_scope.py::test_foreign_uvicorn_survives`
and `::test_port_scoped_helpers` fail with
`_ollama_pid_if_we_started_it: command not found` — the test's own awk-based
extraction of `stop_services()`'s body does not pull in
`_ollama_pid_if_we_started_it` (a separately-defined helper `stop_services`
calls), so the extracted, re-sourced copy the test drives aborts before
reaching any assertion. This predates the `2026-07-29-elite-cli` sprint
entirely (reproduced identically at `42e87f4`, before the sprint's own
first commit) and is unrelated to the sprint's `reset.sh` changes — the
review-fix pass's new `_stop_services_pid_is_instance_owned` exclusion
clause is confirmed to sit correctly OUTSIDE the awk-extracted range (its
closing brace is indented, never at column 0), so it isn't the cause.

Consequence: this file is the natural home for a *unit*-level test of
`stop_services`'s scoping (real fixtures, no real processes, fast), but
because it's broken, `stop_services`'s new instance-exclusion logic (B2)
is exercised ONLY at the driver level
(`tests/cli/restart_driver.sh`'s two sibling-survival scenarios, which spawn
real processes and are confirmed to fail without the fix). That coverage is
real, just not doubled at the faster, more isolated unit level a maintainer
would normally reach for first when touching this function again.

**Why it wasn't done now.** Fixing the awk extraction is a small, focused
change, but it is to a PRE-EXISTING test bug entirely unrelated to the
BLOCK/minor findings this review-fix pass was scoped to address — fixing
it here would be scope drift into a different, older gap.

**What a future sprint needs to decide:** extend the awk range (or switch
to a smarter extraction, e.g. pulling every function `stop_services`
transitively calls) so `_ollama_pid_if_we_started_it` is included, then
add a unit-level scenario for B2's exclusion clause (a fabricated live
instance record, no real process, asserting the fallback pattern match is
suppressed) alongside the existing `test_foreign_uvicorn_survives`/
`test_port_scoped_helpers` cases.

**Mitigation until this is scheduled:** the driver-level B2 coverage
(`tests/cli/restart_driver.sh`) is real and independently confirmed to
fail without the fix — this gap is about test-pyramid shape (missing the
faster, more isolated layer), not about missing coverage entirely.

---

## agenda_watch.py — 4 non-blocking follow-ups from round-11 review

**Filed by:** `sprints/2026-07-26-world-of-debt-finance/` (deals/education/
tracking capability upgrade), REVIEW.md addendum 10, round 11, verdict
WEAK_PASS. None of these are reachable from the World as shipped (the
shipped `scout-patterns.json` has tightly numeric patterns), which is why
round 11 didn't block on them — but a future World author with a looser
pattern could reach all four.

**ASK-19 — candidate-value markdown injection risk in the finding
document.** `src/arail/research/agenda_watch.py` (near the "Candidate
values" section writer) wraps each matched value in a single backtick with
no length cap, while the excerpt directly above it in the same finding is
deliberately fenced as untrusted content. A candidate value containing a
backtick or a newline could break out of that inline-code wrapping and
inject markdown into a document a human reviews and approves. Needs an
operator-authored loose pattern (not any of the shipped ones) to reach.
**Fix shape:** fence candidate values the same way the excerpt above them
already is, plus a length cap consistent with `_MAX_PATTERN_MATCHES`'s
spirit.

**ASK-20 — candidate-extraction result payload has no size cap.**
Round 10 (BLOCK-12's neighborhood) fixed the *misdiagnosis* of a large
result as a killed backtracking pattern (queue-then-join reordering), but
never capped how large a result can actually be. A pattern with a huge
`max_matches` × long per-match text could still produce an very large
payload — no longer misreported as a hang, but still a large read/write
and a large "Candidate values" section in the finding document.
**Fix shape:** cap total serialized candidate bytes in
`_extract_candidates`, truncate with a "(truncated)" marker, same spirit
as the existing per-pattern `max_matches` cap.

**ASK-21 — a slow-but-successful child can be misreported as
backtracking.** In `_extract_candidates_bounded`, if the child is slow to
exit after writing its result (but not stuck matching), the current logic
can discard a valid result and log a "possible catastrophic-backtracking
pattern" warning that doesn't match what actually happened.
**Fix shape:** once `queue.get()` has returned a real result, treat exit
lag as a benign reap-timing detail, not a backtracking signal — only log
the backtracking warning on the path where `queue.get()` itself timed out.

**INFO-23 — BLOCK-12's empty-extraction guard is warn-only, not a
fallback (deliberate, not a gap).** When visible-text extraction empties a
genuinely non-empty fetch, the fix added a loud `_log.warning` rather than
falling back to hashing `raw_text` directly. This is a considered
divergence, not an oversight: falling back to raw bytes would reintroduce
BLOCK-11/BLOCK-9's original problem (a rotating CSRF token or analytics ID
buried in markup counting as "the page changed" on every tick) for the one
World whose extractor happens to have a residual gap, silently
reintroducing noise instead of loudly surfacing a parser bug to fix.
Recorded here so a future reader doesn't "fix" this into a regression.

**Why not done now:** none of the four is reachable from the debt-finance
World's shipped `scout-patterns.json` (tightly numeric APR/percent
patterns), and this sprint's scope was the deals/education/tracking
capability upgrade, not a general hardening pass on `agenda_watch.py`'s
scouting internals for hypothetical future Worlds with looser patterns.

**What a future sprint needs to decide:** whether to fix ASK-19/20/21
proactively (cheap, isolated, no behavior change for existing Worlds) the
next time `agenda_watch.py` is touched, or wait for a concrete World that
actually needs a loose extraction pattern to force the issue.

---

## agenda_watch.py — 2 non-blocking follow-ups from round-13 QA re-verification

**Filed by:** `sprints/2026-07-26-world-of-debt-finance/` (deals/education/
tracking capability upgrade), TEST_REPORT.md "QA round 13", verdict PASS.
Found while adversarially re-verifying the QA-1/QA-2/QA-3 fixes from round
12 (commit `0cdadf1`).

**INFO-24 — the QA-2 slug-hashing fix is an un-migrated rename for a lab
that already ran the pre-fix code.** `_slugish`'s new content-hash suffix
changes every snapshot filename and finding stem. A lab that ran the old
code has `state.json` entries keyed by the old sha with an orphaned
snapshot file under the old name — `_read_snapshot` returns `None` on the
first post-upgrade change. Confirmed non-broken, just non-ideal: the
finding degrades honestly to an **Excerpt** section instead of a unified
diff (not silent, not a crash), and the `Change: <old> → <new>` line stays
correct. The orphaned old-named `.txt` files under
`DATA_DIR/agenda-watch/` are inert residue, never cleaned up.
**Fix shape:** either a one-time migration pass that renames existing
snapshot files to the new slug scheme on first tick after upgrade, or
accept the one-time diff-quality degradation as the cost of the fix (it
self-heals on the very next change per feed) and just clean up the
orphaned files opportunistically.

**INFO-26 — `_safe_write_atomic`'s tmp-file name is fixed per destination,
which is a concurrency assumption, not yet a rule.** The ``.tmp`` staging
name is derived deterministically from the destination path
(``path.with_suffix(".tmp")``), so two concurrent writers to the same
destination could in principle interleave and corrupt each other's write.
Checked and confirmed safe *today*: `agenda_watch.tick()` has exactly one
caller in `src/` (the Librarian's `watch_horizon`, awaited via
`asyncio.to_thread`), so writes are serialized by construction, not by
this file's own locking.
**What a future sprint needs to decide:** if a future feature ever calls
`tick()` from a second call site (e.g. an on-demand "run this watch now"
portal action), either add real locking around the tmp-file write or
derive the tmp name per-call (e.g. a pid/uuid suffix) before that second
caller ships — this is a load-bearing assumption to revisit, not
something to rediscover the hard way.

**Why not done now:** neither is reachable under this sprint's actual
shape (single caller, in-place upgrades are the exception not degrading
unsafely) — filed so a future sprint that changes either assumption
(adds a second `tick()` caller, or needs snapshot continuity across an
upgrade) finds this written down instead of rediscovering it.

---

## PDF text extraction → PKB → librarian scout

**Filed by:** `sprints/2026-08-06-deep-research-world-forge/VISION.md`
(reject-as-scoped verdict on a proposed "Deep Research World Forge"
source mode).

**The gap, independent of the rejected feature.** A PDF dropped into
`lab/pkb/inbox` (the Knowledge tab's drag-drop zone) is filed by `pkb.py`
under the `"papers"` category and then **never read by anything.**
`_PKB_TEXT_SUFFIXES` (`pkb.py:376`) is `.md/.txt/.rst/.csv/.json/.html`;
`librarian_scout._TEXT_SUFFIXES` (line 53) is narrower still —
`.md/.txt/.markdown`. `grep` for `pypdf|PyPDF|pdfminer|fitz|pdftotext`
across `src/` and `pyproject.toml` returns nothing — there is zero PDF
text extraction anywhere in ARAIL. The user-visible surface (a drag-drop
zone, a folder-reveal button, a `"papers"` filing category) implies PDFs
are used. They are not. This is arguably already a defect, not just a
missing feature.

**Why it surfaced now.** An operator asked for a "Deep Research World
Forge" mode (live web research via the Browser agent, feeding a new
World's starting term base) to build a "Quantum" World with current
post-quantum cryptography jargon. VISION.md rejected that proposal — the
Browser agent is a `subprocess.run(["agent-browser", ...])` call the
egress guard structurally cannot see (it patches `requests`/`urllib`/
`httpx`, all in-process), so routing forge through it would make the
existing forge banner's audit promise (`worlds.html:92`) false. Separately,
the actual motivating sources (NIST FIPS 203/204/205, the PQC standards)
are PDFs — so a web-research mode wouldn't have reached the right content
anyway.

**The better wedge, once (if) the need is confirmed.** Add a PDF-to-text
step at PKB ingest (`pypdf` — pure Python, no system deps, no network),
extend both text-suffix allowlists above to cover the extracted text, and
let the existing machinery do the rest: `librarian_scout.mine_candidates()`
already scans `pkb/inbox`/`pkb/sources` for capitalized multi-word phrases
and standalone acronyms — precisely the shape of `ML-KEM`, `SLH-DSA`,
`Module-Lattice-Based Key-Encapsulation Mechanism` — and already routes
mined candidates through evidence accumulation, the ubiquity threshold,
and the Compiled-KB approval gate. No new egress surface (the operator
downloads the PDF themselves), no new consent gate, no Node/npm/Chromium
dependency, and it generalizes past one World: every future World forged
from a specialized corpus (a standards body, a textbook, a paper set) is
served, not just Quantum.

**Why not done now.** VISION.md's reject verdict was contingent on a
pre-registered, falsifiable experiment (three-arm forge coverage test —
local dream / frontier-brain dream / Wikipedia fetch — against a
~20-term PQC checklist) that had not yet been run at time of filing. If
Arm B (frontier brain) scores ≥70% coverage, the existing "Frontier API"
forge-brain toggle already solves the operator's stated problem and this
item drops in priority to "fix the PDF-ingest defect" without the
World-forge framing. Revisit alongside that experiment's result — see
`sprints/2026-08-06-deep-research-world-forge/VISION.md` for the full
decision tree and thresholds.

---

## `pkb._iter_pkb_files` indexes dot-directory contents as ordinary PKB rows

**Filed by:** `sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md`
(W1), confirmed and required for filing by REVIEW.md's tech-debt section.

**The gap.** `pkb._iter_pkb_files` (`pkb.py:391-411`) skips a file only if
`p.name.startswith(".")` — it does not skip files whose *ancestor
directory* starts with a dot. `.wiki-cache/manifest.json`, the
machine-generated wiki index (1.15 MB in the `ai` world, ~230 KB in
`debt-finance`, present in every world), is therefore indexed as an
ordinary PKB row alongside real user/agent content. Confirmed live: of the
889-row corpus measured in this sprint, 19 rows come from
`.wiki-cache/manifest.json` files (5 worlds, 1 each — actually one per
world where the wiki has been built).

**Why it wasn't fixed here.** `pkb.py` is on this sprint's explicit
"must NOT touch" list (ARCHITECTURE.md §"What the builder must NOT touch"
#6) — it stays byte-identical to baseline `8cb5760` unless and until the
embedding-provider gate passed, and even then the architecture scopes the
allowed `pkb.py` edits to the C1 error contract, C2's lazy-index removal,
and the C4/W9 embed-call-site swap specifically, not general bug fixes
picked up along the way. This is a genuine, independent production defect
that predates this sprint and is orthogonal to embeddings.

**Impact today:** neutral to search *quality* under `hash_embedding`
(both arms in this sprint's A/B saw the same distractor rows, so it did
not bias the measurement — confirmed in REVIEW.md's tech-debt section).
It does waste embedding calls once nomic is live (a 1 MB JSON blob costs
one more embed call per world, capped at 4096 chars like everything else,
so the cost is bounded but non-zero and pointless) and it pollutes
`source_kind="user"` search results with an internal cache artifact that
was never meant to be searchable.

**What a future sprint needs to do:** exclude any path with a
dot-prefixed *directory component* (not just a dot-prefixed filename) in
`_iter_pkb_files`, or explicitly denylist `.wiki-cache/` and
`.cache/` by name (both already exist as sibling exclusions for other
reasons — `.cache/` holds the LanceDB table itself). Needs a regression
test asserting `.wiki-cache/manifest.json` is absent from `index_all()`'s
row count before and after the fix, since silently changing row counts
without a test would make the next embedder-migration measurement
non-reproducible against this sprint's baseline.

---

## Docs-registry corpus slice is unmeasured by the Tier 1.2 A/B, but is re-embedded by the swap

**Filed by:** `sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md`
(W1 design decision), required for filing by REVIEW.md's spec-adherence
section (acknowledged drift #2) and required action 1.

**The gap.** `pkb.index_all()` appends `pkb._build_docs_rows()` — a
*global*, non-world-scoped set of rows drawn from `docs_registry.all_docs()`
— to every world's PKB rows before writing the vector table
(`pkb.py:529-530`). The Tier 1.2 A/B harness
(`scripts/eval/retrieval_ab.py`) deliberately excludes this slice: since
`_build_docs_rows()` takes no `root` parameter, the same docs rows would
appear identically in all five worlds' harness corpora, making "recall@5
per world" incoherent (a docs page would simultaneously count as "in"
`ai`, `qukaizen`, `debt-finance`, etc.). REVIEW.md agreed with this call.

**Consequence, carried forward explicitly (not silently):** the published
recall@5 numbers (hash 50.0% / nomic 90.6%, Δ +40.6pp) say nothing about
retrieval quality over the docs-registry slice specifically. When W9 swaps
the embedding provider at the `index_all`/`_build_docs_rows` call sites,
it will re-embed this slice too, on the strength of the *general* result
(nomic beats hash on prose-and-glossary content of the same rough shape:
markdown pages with titles, short bodies, procedural or definitional
prose) rather than a slice-specific measurement. Risk is assessed as low
— same embedder, same embed-input construction, same document shape — but
it is an unmeasured extrapolation and should be named as such wherever
W9's rationale is written up, not treated as directly covered by the A/B.

**What a future sprint could do, if this matters in practice:** extend
`scripts/eval/retrieval_ab.py` with a `--include-docs` mode that scores
the docs-registry rows as their own pseudo-world (or folds them into
`root`, since `docs_registry` content is root-scoped conceptually) so the
docs slice gets its own recall@5 number rather than inheriting the
PKB-only result by assumption.

---

## `pkb_provenance.py`'s JSON sidecar is a second-best `content_refs`

**Filed by:** ARCHITECTURE.md §"Tech debt assessment" (conditional),
required to be filed "at build time" — this is that filing, from
`sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` W7/W9.

**The gap.** The rejected 2.0 consolidated store's `content_refs` table
records `embedding_model`/`embedding_dim` per row, transactionally, as
part of the same write. The 1.x per-instance `pkb_pages` tables have no
such column, so `pkb_provenance.py` (new this sprint) is a JSON file
sitting next to the LanceDB table instead: written last, read on load,
compared against the current spec. It does the job (C4: no query served
from a table whose provenance disagrees with the spec), but it is a
second-best solution to a problem the 2.0 store already solves properly,
and it is one more piece of on-disk state that can theoretically drift
from the table it describes (e.g. a `pkb_pages.lance` directory copied
without its sidecar).

**What a future sprint should do:** retire `pkb_provenance.py` if/when
the consolidated 2.0 store cutover is revisited (see
`sprints/2026-08-08-arail2-declarative-persistence/INTEGRATION.md` and
VISION.md's rejection rationale for that cutover) — `content_refs`
subsumes it. Until then, `pkb_provenance.py`'s three functions
(`write`/`read`/`agrees_with_spec`) are the whole surface; a future
migration only needs to reimplement `agrees_with_spec`'s semantics
against `content_refs` rows.

---

## Two vector spaces in one lab: `pkb_pages` (nomic) vs `wiki_nodes` / `agent_workflows` / `experiments` (hash)

**Filed by:** ARCHITECTURE.md §"Tech debt assessment" (conditional,
"Also filed") and REVIEW.md's amendment 1 — required to be filed at
build time. This is that filing, from
`sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` W9.

**The gap.** After the Tier 1.2 embedder swap, `pkb_pages` is embedded
with nomic-embed-text (768-dim, real semantic vectors). `wiki_nodes.py`'s
own hash table, `agent_workflows`, and `experiments` all still use
`vector_index.hash_embedding` (128-dim, a SHA1 token-hash projection).
This is safe today — the tables are physically separate LanceDB tables/
directories, so nothing cross-contaminates — but it means "semantic
search" now means two different, incompatible things depending on which
surface of the lab you're searching, and only one of them (`pkb_pages`)
carries a provenance sidecar (C4) recording what it actually is.

**Corrected framing (REVIEW.md amendment 1, measured, not assumed):**
`hash_embedding` is not kept around because it is competitive on any
retrieval axis this sprint measured (see
`sprints/2026-08-08-arail2-tier1-integration/RESULTS.md`'s exact-token
collision diagnostic — hash lost even the class it was assumed to win).
It is kept **only** because `wiki_nodes`/`agent_workflows`/`experiments`
still call it directly and swapping those three call sites was
explicitly out of scope for this sprint (A5 in ARCHITECTURE.md).

**What a future sprint should do:** if `wiki_nodes`/`agent_workflows`/
`experiments` search quality becomes a live complaint, measure them the
same way this sprint measured `pkb_pages` — a fixture, an A/B, a
15-point-or-larger bar — rather than assuming the `pkb_pages` result
transfers. Their content shape (wiki term pages, workflow logs,
experiment records) differs enough from PKB prose that the `pkb_pages`
number is not strong evidence either way. Extend each with its own
`pkb_provenance`-style sidecar (or the shared module, generalized past
"pkb_pages" as a hardcoded table name) if/when they are swapped, so "two
vector spaces in one lab" stays a recorded, provenance-checkable fact
instead of reverting to being an undocumented discovery.

---

## `pkb_index`'s degraded state is a module global; PKB roots are per-World

**Filed by:** REVIEW2.md's tech-debt section (required to be filed before
PASS), `sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` build3.

**The gap.** `pkb_index._degraded_codes` (and, before build3's reason-
scoping fix, the single `_degraded`/`_degraded_reason` pair) is one dict
shared by the whole process, but `ensure_ready` caches `_pkb_root_cache`
and early-returns on `_initialized` — meaning in a process that touches
more than one PKB root, one World's degraded status can describe another
World's table. Concurrent-Worlds usage (`./arailctl start --world <slug>`)
runs each World as its own OS process today (per
`docs/concurrent-worlds.md`), which is what makes this survivable right
now — one process, one root, one meaningful global. Per the operator's
own recorded usage pattern (serial, not concurrent Worlds — see
`learnings/` cross-product memory), this has not bitten anyone yet.

**What a future sprint needs to do, if concurrent in-process multi-root
access is ever introduced:** key `_degraded_codes` (and `_pending`,
`_timer`, `_pkb_root_cache`) by `pkb_root` instead of being process-global
singletons. This is a real refactor (the debounce timer machinery assumes
one root too), not a one-line fix — scope it as its own sprint if/when the
concurrent-Worlds architecture starts sharing a process.

---

## `tests/conftest.py`'s suite-wide embedding stub hides a hash-vector regression

**Filed by:** REVIEW2.md's tech-debt section (required to be filed before
PASS), `sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` build3.

**The gap.** The autouse `_stub_embedding_provider` fixture (added W6,
`tests/conftest.py`) replaces `arail.dbspec.embed.embed_documents`/
`embed_query`/`embed` with a deterministic `hash_embedding`-based fake for
every test not marked `@pytest.mark.requires_ollama`. This is the right
call for FM18 (a CI runner with no Ollama must not turn every pkb test
into an integration test) — but it means a hypothetical future regression
where production code silently falls back to `hash_embedding` instead of
calling the real `embed_documents` symbol would be **invisible** to the
entire test suite: the stub's own output looks identical in shape to what
a real accidental hash-vector fallback would produce.

**Mitigation already shipped, build3:** one non-stubbed guard test
(`tests/test_w9_embedder_swap.py::test_index_all_calls_the_real_embed_documents_symbol`)
asserts `index_all` calls the actual `arail.dbspec.embed.embed_documents`
function object (via `unittest.mock.patch`'s call-target identity, not the
stub), so a regression that swapped the call site back to
`hash_embedding` would fail this one test even though every other
pkb-related test in the suite would stay green.

**What a future sprint could do, if this matters more:** extend the guard
pattern to `_build_row`/`pkb_reembed.run` (the other two production call
sites), or add a suite-wide post-run assertion that greps the coverage
report for the `hash_embedding` symbol's call sites and fails if any of
the three production embed call sites show up.

---

## `pkb reembed`'s `.bak-<ts>` backups accumulate with no pruning

**Filed by:** REVIEW2.md's tech-debt section (required to be filed before
PASS), `sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` build3.

**The gap.** Every successful `./arailctl pkb reembed` that replaces an
existing live table renames the old `pkb_pages.lance` to
`pkb_pages.lance.bak-<unix-ts>` rather than deleting it — the documented
rollback path (`docs/cli.md`'s `reembed` section: "move the `.bak-<ts>`
dir back over `pkb_pages.lance`"). Nothing prunes these. A World that gets
re-embedded repeatedly (e.g. after several spec/model changes, or after
an operator scripts periodic reembeds) accumulates one full-size backup
directory per run, unbounded, with no CLI surface to list or clean them
and no mention of the accumulation in `docs/cli.md` beyond the rollback
instructions.

**What a future sprint should do:** either (a) a `--keep-backups N` flag
defaulting to something like 1–2, pruning older `.bak-<ts>` dirs after a
successful swap, or (b) fold pruning into `./arailctl db optimize`
(ARCHITECTURE.md's rollback plan already floats this: "`db optimize` may
claim them later"), whichever surface makes more sense once the
consolidated-store question is revisited. Until then, an operator running
`pkb reembed` repeatedly should expect disk usage proportional to run
count × corpus size, and should manually `rm -rf` old `.bak-<ts>` dirs
once they've confirmed a reembed succeeded.

---

## C1's `/knowledge` banner and Buddy context-header wiring — deferred

**Filed by:** REVIEW2.md required action 4 (explicit-deferral option),
`sprints/2026-08-08-arail2-tier1-integration/SPRINT.md` build3 decisions
log, `sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` build3.

**The gap.** C1 in ARCHITECTURE.md named three surfaces for
`retrieval_status()`: the search API payload, the `/knowledge` banner, and
Buddy's context-header line ("an agent must not be handed keyword-only
results while the UI claims semantic retrieval"). Build3 wired the first
(`/api/pkb/search` now sets `X-Retrieval-Status`/`X-Retrieval-Reason`
response headers when degraded — see
`tests/test_pkb_search_api_status.py`). The other two were not: `/knowledge`
is itself a 307 redirect to `/dac` today (the surface moved since C1 was
written), and Buddy's system-prompt/context-builder wiring is a separate
code path (`search_for_agents` → whatever assembles Buddy's LLM context)
that needs its own read-and-scope pass rather than a same-commit addition
alongside two BLOCK fixes.

**What a future sprint needs to do:** (a) find the current DaC/`/dac`
knowledge surface's template and add a degraded banner there, reading
`pkb.retrieval_status()` the same way the search endpoint now does; (b)
find where Buddy's (and any other agent's) system prompt is assembled
from `search_for_agents()` results and prepend a one-line honesty note
("semantic search is degraded: <reason>; results below are keyword-only")
when `retrieval_status()` reports not-ok. Both are small once located —
the backend primitive (`pkb.retrieval_status()`) already exists and is
tested; this is purely "find the two remaining call sites and read one
tuple."

**Hard constraint added by QA (TEST_REPORT.md QA-6, 2026-08-08):** (b)
must NOT be built as a naive read of `retrieval_status()` on its own. The
Compiled-KB gate ships on by default with nothing approved on all six of
the operator's real PKB roots (pre-existing, Phase 1 audit A36,
`compiled_kb.py:109` failing closed) — so `search_for_agents` /
`pkb.search(approved_only=True)` returns `[]` **before**
`_semantic_search` ever runs, and no degraded code is ever set.
`retrieval_status()` therefore reports `(True, "")` — healthy — on
exactly the same legacy World where Buddy gets zero hits. A context
header built exactly as C1 specifies, wired straight to
`retrieval_status()`, would print a **false "retrieval healthy"** right
next to zero results. Whoever builds (b) must first make the agent path
itself consult the gate/approved-count state (not just the embedding
degraded-codes) before trusting `retrieval_status()` for this surface —
see `tests/test_qa_tier1_buddy_retrieval.py::test_agent_retrieval_returns_nothing_and_reports_healthy_on_a_legacy_world`,
which pins the hazard.

---

## `VectorIndex._table()` is re-opened three times per PKB query

**Filed by:** REVIEW3.md's performance assessment, per the coordinator's
explicit "do not optimize now" instruction —
`sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` build4.

**The gap.** `pkb._semantic_search` now calls `idx.count()`, then
`idx._table()` directly for the read-path health check, then
`idx.search_vector()` opens the table a third time internally
(`VectorIndex._table()` is not memoised — it re-resolves the table handle
on every call, by design, since `VectorIndex` instances are typically
short-lived per-call wrappers). The health check itself is not the cost
(measured at 0.105 ms, one small provenance-sidecar JSON read).

**Correction (QA, TEST_REPORT.md, 2026-08-08):** REVIEW3's original
measurement of `_table()` at 7.5 ms of a 20.8 ms `pkb.search()` call was
overstated — QA measured the real call directly and got **0.39–1.21 ms**
per `_table()` open on realistic corpora, roughly an order of magnitude
lower. The triple-open shape described below is still real and still
worth fixing eventually, but it is not the hot-path emergency the
original number implied; treat this entry as lower urgency than its
original framing.

**Why it wasn't fixed now.** The coordinator ruled explicitly: measure
and file, don't optimize this sprint — the fix (passing the
already-opened `table` object into `search_vector`, or memoising
`VectorIndex._table()`) is a real change to a hot path that deserves its
own focused pass with its own before/after measurement, not a
reflexive addition to an already-large integration sprint's diff.

**What a future sprint should do:** either (a) give `_semantic_search`
a variant that opens the table once and threads it through
`check_read_path_health` and `search_vector` explicitly, or (b) add a
small opt-in cache to `VectorIndex` (e.g. `_table_cache: Any | None`,
invalidated by a generation counter or simply never reused across
`VectorIndex` instances, since each call site already constructs a fresh
one). `open_table` cost is expected to grow with LanceDB fragment count,
so this gets worse, not better, as a World's PKB grows — worth doing
before a World's corpus gets large enough for it to matter in practice.

---

## `pkb_reembed`'s shadow-build verification is cardinality-only, not content-aware

**Filed by:** REVIEW3.md's "also fix" #3 (coverage half implemented; the
underlying check itself was explicitly not upgraded) —
`sprints/2026-08-08-arail2-tier1-integration/BUILD_LOG.md` build4.

**The gap.** Both shadow-completeness checks BLOCK-2 added
(`pkb_reembed.py`'s pre-swap check and its `--resume` checkpoint check)
compare ROW COUNTS — `shadow_row_count == total` and `shadow_row_count ==
len(completed_paths)` — never the actual path *identity* or row
*content*. `tests/test_pkb_reembed.py::
test_shadow_verification_is_cardinality_only_documented_limitation` pins
the concrete, reproduced blind spot: a `--resume` checkpoint that
correctly names some real corpus paths as already-completed, backed by a
shadow table that genuinely has rows at those same paths but with a
**stale vector** (as if embedded from old file content and never
re-verified), is trusted verbatim into the swapped-in live table. The
counts are satisfied, so nothing discards it. (A related but already-
closed scenario: a checkpoint naming entirely WRONG paths as completed is
actually still caught today — `remaining` is computed by path-set
membership against the real corpus, not by subtracting a count, so wrong
paths cause every real row to be re-embedded and the final
`shadow_row_count != total` check trips correctly. The surviving gap is
narrower: matching paths, unverified content.)

**What a future sprint should do:** add a
`set(shadow_table.to_pandas()["path"]) == set(completed_paths)` check
alongside the existing count checks (cheap — one more `to_pandas()`
column read), and/or re-embed and diff a sample of "already completed"
rows on `--resume` rather than trusting them unconditionally. The
narrowest fully-correct fix would key resumption on a hash of each row's
`embed_input` rather than path alone, so a file whose *content* changed
between the interrupted run and the resume is also caught — currently
neither the count check nor a hypothetical path-set check would catch
that case either.

---

## Chat tab compute-source pills: honest-disabled, not send-path wired

**Filed by:** `sprints/2026-08-11-two-slot-chat-models/` (explicit
operator scope decision, made before implementation via AskUserQuestion
— "Honest-disable" over building full multi-provider chat send in the
same sprint).

**The gap.** The Resident/Deep pickers' "Provider · run on" pills
(Claude, NVIDIA, OpenRouter, HF, Custom) are real UI with a real,
truthful disabled state (`sources[].wired`) — clicking a non-`my_machine`
pill shows "not wired to chat send yet" instead of silently failing at
send time the way it used to. But the send path itself
(`/api/chat/stream` and friends) still only really talks to `my_machine`
in practice; wiring an actual cloud round-trip through the two-slot chips
was explicitly out of scope for this sprint.

**What a future sprint should do:** wire at least one non-local source
(Claude is the obvious first target — token-paste UX and
`_provider_token`/`_fetch_provider_models` plumbing already exist for the
gallery view) through to a real `/api/chat/stream` call, then flip that
one source's `wired` flag. No owner or target date assigned yet.

---

## `arail.dbspec.ensure`'s Atlas-free replay has no CI job proving it stays in sync with `atlas schema diff`

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md`
§8 "Tech debt" — required follow-up ticket #1 before merge.

**The gap.** `arail.dbspec.ensure` is a second, hand-written schema-
application path alongside the existing Atlas-driven `./arailctl db
apply`. ARCHITECTURE.md test 4 ("after `ensure_db(apply=True)`, `atlas
schema diff` reports no statements") is the proof that the two paths
never diverge — but it is dev-only and skipped whenever the `atlas`
binary is absent, which is every CI runner and every user machine
(Assumption 1: atlas is a developer tool, not a user dependency). So the
one test that would catch `ensure.py`'s replay logic drifting from a
future `schema.hcl` change never actually runs in CI today.

**What a future sprint should do:** add an `atlas`-bearing CI job
(install the binary the way a maintainer would, `brew install
ariga/tap/atlas` or the Linux equivalent) that runs `pytest -k
test_schema_fidelity_atlas_diff` (or whatever the test ends up named)
specifically, separate from the main test matrix so a missing `atlas`
binary elsewhere doesn't silently skip real coverage.

---

## `start`'s DB readiness check warns-and-continues; promote to a hard gate once `arail.db` has a runtime reader

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md`
§4.5 and §8 "Tech debt" — required follow-up ticket #2 before merge.

**The gap.** `./arailctl start` calls `ensure_db(this_instance_data_dir,
apply=True)` and, on `blocked`/`ahead`/`diverged`/`unavailable`, warns,
names the exact fixing verb, and **continues booting** rather than
refusing. This is deliberate for now: as of this sprint, nothing outside
`src/arail/dbspec/` reads `arail.db` at runtime (verified by grep —
`repo.py`, 500 lines, has zero runtime consumers), so gating boot on an
inert store would trade a real outage for a theoretical one.

**What a future sprint should do:** the moment any runtime code path
starts reading `arail.db` (the first `repo.py` consumer, or any future
feature built on the relational store), revisit `start`'s behavior on a
non-`ok` DB state: it must become a hard readiness gate — `start` exits
non-zero rather than booting a lab whose dependent service is broken.
Until then, this warn-and-continue is the correct, disclosed call, not
an oversight.

---

## `status --json=instances`'s byte-compatibility guard is self-referential, not a committed golden

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/REVIEW.md`
round 1, ASK-3 — non-blocking, filed per the review's own instruction.

**The gap.** ARCHITECTURE.md's F7/test 27 called for `--json=instances`
compared against a **committed golden file**, byte-for-byte. What shipped
instead (`tests/cli/status_driver.sh`'s T12/T27 scenario) is an
internal-consistency check: (a) no `db`/`origin` key ever appears in a
`--json=instances` row, and (b) stripping those same keys back out of
`--json`'s (full-mode) `.instances` reproduces `--json=instances`
exactly. That's a real, useful property — it does catch this sprint's
actual risk (the db/origin augmentation leaking into the byte-compatible
mode) — but it's self-referential: a change that broke both modes
**identically** (e.g. renamed `slug` to `id` in both renderers at once)
would pass this check while still breaking every external script that
parses `--json=instances`.

**What a future sprint should do:** add a small committed golden file
(a fixed instances-array fixture, or a snapshot of a known scenario's
`--json=instances` output) and assert byte-for-byte equality against it,
the way F7 originally specified — independent of whatever `--json=full`
happens to produce in the same run.

---

## `tests/cli/status_driver.sh` (and the whole CLI-contract driver layer) cannot run in CI or any worktree without a real `.venv`

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/REVIEW2.md`
round 2 — "the meta-pattern... this is the second consecutive round where
the shell layer's only real test hid something, and both times I found
it by hand-running the driver against an external venv." Required action
4 (partial — the other half is the `make_fake_venv` hardening entry
below).

**The gap.** `tests/cli/status_driver.sh` (and its siblings —
`install_driver.sh`, `root_start_driver.sh`, etc.) self-skip entirely
whenever no `.venv` with `arail` importable is found — which is every CI
runner today and every builder worktree in this sprint's two rounds so
far. That means the entire CLI-contract layer — the shell control flow
that actually wires `ensure_db` into `install`/`start`/`status`, the
exit-code contract, the byte-compatibility guarantees — has never once
run in an automated, repeatable way. Both round 1 and round 2 of this
sprint's review only caught real regressions (two broken test
assertions in round 1; a live exit-code regression, T10/BLOCK-5, in
round 2) because the architect hand-ran the driver against an external
venv on their own machine. That is not a repeatable safety net.

**What a future sprint should do:** give CI (or at minimum, the
project's provisioned-checkout workflow) a real path to actually
`pip install -e .` and run these drivers — not `pytest -k` skip-detection,
an actual `.venv` build step ahead of the CLI-driver suite, gated behind
whatever's cheapest (a dedicated CI job, a pre-commit hook on a
provisioned machine, or at minimum a documented "run this before every
merge" step in `docs/cli.md`/`AGENTS.md`). Until this exists, treat every
change to `install.sh`/`start.sh`/`status.sh`/`scripts/lib/instances.sh`
as unverified by CI regardless of how many unit tests pass.

---

## `tests/cli/lib.sh`'s `make_fake_venv` symlinks straight into the real venv's site-packages — no isolation

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/REVIEW2.md`
round 2, "The `make_fake_venv` footgun" section. Recommended follow-up,
not required before merge — a prominent warning was added at the
symlink site instead (`tests/cli/lib.sh:make_fake_venv`) as the
required-before-merge mitigation.

**The gap.** `make_fake_venv` does `ln -s "$REAL_VENV/lib" "$fake/.venv/lib"`
— the ENTIRE `lib/` directory (site-packages and all) is one symlink
into the real, operator-installed venv. Any test scenario that renames,
edits, or writes through a path reached via that symlink mutates the
real installation, not a fixture. A draft scenario during this sprint's
round 2 did exactly this (renaming `ensure.py` to simulate an import
failure) — caught and discarded before it ran, but the mistake is one
`git blame`-invisible edit away from happening for real, and the fix
(a comment) only helps someone who reads it.

**What a future sprint should do (either, whichever is cheaper when
someone can actually test it against a real venv):**
1. **Per-package symlinks** — instead of symlinking the whole `lib/`
   dir, create `$fake/.venv/lib/.../site-packages` as a REAL directory
   and symlink each top-level package/module inside it individually.
   A scenario that renames `arail/dbspec/ensure.py` inside the FAKE
   tree only ever touches the fake symlink to that one file, never the
   real `arail/` package directory itself, IF the scenario is careful
   to `readlink` before renaming — still needs care, but shrinks the
   blast radius from "the whole real venv" to "one file."
2. **Read-only tree** — mount or `chmod`-protect the real venv's
   `site-packages` (or a copy of it) so any write through the symlink
   fails loudly (permission denied) instead of silently succeeding.
   Simpler, but chmod'ing a symlink's target has surprising semantics
   across platforms and needs verifying against a real venv before
   shipping — not done in this round for exactly that reason (no
   `.venv` available to test against safely).

Not implemented this round because it touches shared test-harness
infrastructure used by every CLI driver, and verifying it doesn't break
anything requires a real `.venv` to run against — unavailable in this
worktree, and the coordinator's constraints for this sprint explicitly
forbid experimenting against the operator's real installation.

---

## `provisioning.evaluate_all` — a predicate can return an `Assertion` under another mechanism's key

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/TEST_REPORT.md`
round 4, QA-11 (LOW, cosmetic) — explicit coordinator ruling "file, do not
fix" in round 5.

**The gap.** QA-7 (round 4) closed the front door: `register()` now
refuses a duplicate key. The back door is still open — a predicate
registered under its own key may *return* an `Assertion` object whose
`.key` field names a *different*, already-registered mechanism (e.g. a
plugin registered as `"my_plugin"` whose predicate returns
`Assertion("relational_store", "required", True, True, "all good", "")`).
`evaluate_all` does not check that a returned `Assertion.key` matches
the key it was registered under, so this produces two rows for one
mechanism in `arail.provisioning/v1` and `doctor`'s printed table — one
of them potentially a fabricated "healthy" row masking the real one.

**Why this stays LOW / non-blocking:** it cannot flip an exit code.
`doctor._FINDINGS` is a list and the degrade decision is `any(not
f.ok and ...)` over all of them — the genuine failing row (registered
under its real key) still degrades the run even if a duplicate,
healthy-looking row also exists under the same key. It is a
reporting-integrity wart (confusing output), not a silencing mask.

**What a future sprint should do:** in `evaluate_all`, after confirming
`isinstance(result, Assertion)` (QA-10's fix), also check
`result.key == key` (the key it was registered under) and, on mismatch,
either substitute a "key mismatch" finding (mirroring QA-10's
non-Assertion handling) or force `result.key` back to the registered
key before appending. `tests/test_qa_provisioning_generalize.py::test_a_predicate_cannot_impersonate_another_mechanisms_key`
is already written against the correct behavior and is left failing on
purpose — it will pass once this lands.

---

## `status.sh`'s human render gates the `db:` line on liveness only — ASK-6's remaining half

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/TEST_REPORT.md`
round 4, QA-13 (MEDIUM) — explicit coordinator ruling "file, do not fix"
in round 5. Mitigated, not blocking.

**The gap.** ASK-6's `--json`/`--json=full` half is fixed (round 4,
`59947f4`): a tampered migration ledger (`diverged`) survives the
missing-data-root suppression and correctly degrades a *live* lab to
exit 3 with the reason present. What QA verified still doesn't match
ARCHITECTURE.md §4.4's own contract text is the **human render**: on a
tampered checkout with **nothing running** —

```
--json : root.db.state = "diverged", detail names the hash mismatch
human  : nothing.  "root lab: not running — ./arailctl start"
```

`status.sh` gates the human `db:` line on `state == "live"` /
`root_is_live`, but §4.4 says the line should print "only when the lab
is up **or the state is not `ok`**" — i.e. a non-`ok` db state should be
visible in the human view even for a lab that isn't currently running,
which the code does not do. Verified in an independent topology (not
just the ledger driver's own killed-PID scenario) so the finding isn't
an artifact of driver plumbing.

**Why this is mitigated, not a live safety hole:** `start` itself warns
on `diverged` at boot (`start.sh`'s db-ensure step, `_instance_db_ensure`)
— the moment the tampered SQL would actually be considered for replay,
the operator sees a warning naming the exact verb. And the exit code
`status` gives when nothing is running (`4`, "nothing running") is
truthful on its own terms — it is not lying about liveness, it is simply
not surfacing a fact about a stopped lab's database that ARCHITECTURE.md's
own text says it should.

**What a future sprint should do:** either (a) change `status.sh`'s
human renderer to match §4.4's literal text — print the `db:` line
whenever `state != "ok"`, regardless of liveness — and re-verify this
doesn't reintroduce chatter on the many legitimate non-live, `pending`/
`unavailable` (now correctly non-degrading) states this sprint's own
BLOCK-5/ASK-6 work spent two rounds getting quiet; or (b) narrow
ARCHITECTURE.md §4.4's contract text to match the code's actual,
considered behavior (report non-`ok` db states in the human view only
for a currently-live lab) and document why "nothing running" is treated
as softer than "up but degraded" for this specific line. Either is
defensible; shipping without choosing is not — same discipline BLOCK-5
already established for the sibling suppression decision.

---

## `daemon_active()`'s Darwin-only guard makes `status` misreport a genuinely-supervised Gentoo/OpenRC lab as "foreground"

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md`
§10, finding 1 (PR #181 CI, 2026-08-10) — required filing alongside the
platform gate on `tests/cli/status_driver.sh`'s `T3/daemon-a`/
`T3/daemon-b` scenarios.

**The gap.** `scripts/lib/instances.sh:464`'s `daemon_active()` opens
with `[[ "$(uname -s)" == "Darwin" ]] || return 1` — a guard installed
because `scripts/install-daemon.sh:35` genuinely refuses to install
launchd supervision on non-Darwin ("launchd supervision is macOS-only").
That guard is correct for launchd specifically. But
`scripts/gentoo-bootstrap.sh:182` installs OpenRC services
(`rc-update add arail-portal default`) — so a Gentoo lab CAN be, and
often IS, supervised, just not via launchd. `daemon_active()`'s
Darwin-only early-return means `status` reports
`supervision.mode: "foreground"` for such a lab regardless of whether
OpenRC is actually running it — a genuine, pre-existing observability
defect, unrelated to and out of scope for this sprint (persistence, not
supervision).

**Why this doesn't invalidate the platform gate on the test:** the gate
on `T3/daemon-a`/`T3/daemon-b` is scoped narrowly to "launchd supervision
cannot exist on Linux, so a scenario that plants a `LaunchAgents` plist
and stubs `launchctl` cannot represent a reachable state there" — that
claim is true regardless of this OpenRC gap. The gate is NOT "Linux has
no supervision," and no comment or code in this sprint's changes asserts
that; this ticket exists specifically so nobody mistakes the narrow gate
for the broader (false) claim.

**What a future sprint should do:** extend `daemon_active()` (or add a
sibling check) to detect OpenRC supervision on Linux
(`rc-service arail-portal status`, or equivalent — mirroring
`gentoo-bootstrap.sh`'s own install path) and report `supervision.mode`
accurately for that case, the same way the Darwin path already does for
launchd. Any new CLI driver scenario for this should follow this
episode's own lesson: gate the scenario on the platform the mechanism
actually requires, and say so loudly if the test is skipped elsewhere
(see `status_driver.sh`'s `skip_scenario()` helper, added for exactly
this pattern).

---

## The CLI driver suite's CI coverage is narrower than "all of `tests/cli/*.sh`"

**Filed by:** `sprints/2026-08-10-arail2-persistence-instantiated/ARCHITECTURE.md`
§10 — "the still-open CI-runnable driver path from REVIEW3," which this
sprint's PR #181 CI episode is itself the argument for.

**What's already done:** `.github/workflows/db-ensure-ci.yml` (this
sprint) wires four sprint-specific drivers into CI —
`status_driver.sh`, `qa_db_collector_driver.sh`,
`qa_db_seamless_driver.sh` (hard gates) and `qa_db_ledger_driver.sh`
(`continue-on-error`, QA-13's one filed gap) — plus the sprint's Python
QA suite, against a real `.venv` created in-job via `pip install -e
".[dev]"`. That closes the specific, merge-blocking instance of this gap
(QA-8: a driver run from the wrong source tree can report green while
testing entirely different code).

**What's still open.** The rest of `tests/cli/*.sh` —
`install_driver.sh`, `root_start_driver.sh`, `restart_driver.sh`,
`picker_driver.sh`, `warmup_driver.sh`, `verbs_driver.sh`,
`color_driver.sh`, `reset_full_models_driver.sh`, `qa_edge_driver.sh`,
and any future addition to this directory — has never run in CI at all.
Every one of them self-skips silently without a `.venv` (the same
`_cli_test_find_venv` pattern `status_driver.sh` uses), so a contributor
who never happens to hand-run them locally against a real venv gets zero
signal, on every PR, forever.

**This episode is the argument for closing it.** PR #181's CI run
surfaced a genuine, previously-undetected platform assumption
(`T3/daemon-a`/`T3/daemon-b` encoding a macOS-only fixture with no
gate) the moment the driver ran somewhere other than a macOS
developer's own machine — exactly the class of defect a CI-invisible
test layer cannot catch, on any driver, indefinitely. The four drivers
now in CI are the ones this sprint happened to touch; the other nine are
exposed to the identical risk and nothing currently guards them.

**What a future sprint should do:** extend `.github/workflows/
db-ensure-ci.yml` (or add a sibling workflow, matching its established
`pip install -e ".[dev]"` + real-`.venv` shape) to run the remaining
`tests/cli/*.sh` drivers, and audit each one for the same class of
platform assumption `T3/daemon-a` had — a scenario is legitimate to gate
on a platform only when the PRODUCT is gated at the same boundary and
says so (this sprint's own standing rule, ARCHITECTURE.md §10); anything
gated merely to make CI green needs fixing, not skipping.

---

## Autoresearch durability — the Tuning loop's git ledger over the long term

**Filed by:** `docs/plans/autoresearch-integration.md` (2026-08-16 audit,
branch `qukaizen/autoresearch-integration-plan-e1a5c3`), prompted by the
operator question "is this really working for someone long term?"

**The gap.** The Tuning loop (`src/arail/experiments/autoresearch.py` +
`git_ops.py`) writes a branch-per-experiment ledger into the ARAIL
checkout itself. Every safety rail on the *individual* operation is in
place — 4-file whitelist, clean-tree gate, env flag, explicit staging,
no push, no force, no reset. What is missing is any notion of the ledger
as something that **accumulates**. Five hazards, all structural rather
than observed (the loop has never been run end-to-end on the operator's
machine — `git branch --list 'autoresearch/*'` returns 0 and no bench
`.jsonl` exists):

- ~~**H1 — baseline commit lands on the user's current branch.**~~
  **FIXED 2026-08-16.** Baseline now commits on its own
  `autoresearch/baseline-<ts>` branch; variants branch from that; the
  loop restores the user's original branch in a `finally`. Guarded by a
  `restore_to` sentinel so the restore (which runs `git checkout -- .`)
  can never fire on the dirty-tree abort and eat the user's work. Four
  regression tests in `tests/test_experiments.py`.
- ~~**H2 — `./arailctl update` breaks after H1.**~~ **Resolved by the
  H1 fix.** Remaining nicety: `./arailctl update` could detect and
  explain pre-existing local commits instead of surfacing a raw
  `git pull --ff-only` refusal.
- ~~**H3 — `autoresearch/*` branches accumulate forever.**~~ **FIXED
  2026-08-17** — `./arailctl autoresearch prune`, dry-run by default,
  keep-gated (wins/current/unclassifiable/newest-N/young all survive),
  re-validated at delete time, receipt-with-SHA written before each
  deletion.
- ~~**H4 — committed bench files grow without bound.**~~ **FIXED
  2026-08-17** — `./arailctl autoresearch rotate`, dry-run by default,
  archive-append + atomic replace, nothing discarded. Deliberately NOT
  automatic inside the loop; see the plan doc for why, and for the cost.
- **H5 — clean-tree gate vs. a blueprint people fork.**
  `assert_clean_tree()` blocks the loop on any in-progress user edit.

Plus a packaging note: `git_ops._repo_root()` walks four parents from
`__file__`, which is correct for a checkout and for `pip install -e`, but
lands outside any git repo under a non-editable install.

**H0, found 2026-08-17 while building the H4 fix and more serious than
any of the above: the loop could not complete a single pass.** Two of the
four whitelisted paths live under `lab/data/`, which `.gitignore:42`
excludes wholesale; `commit_experiment` ran a plain `git add` on them,
which exits 1 and stages nothing, and `_run` uses `check=True`. That
raised `CalledProcessError` from the baseline commit — the first commit
of every pass, whose caller catches only `GitSafetyError`. Fixed by
force-adding (safe because `-f` can only reach `ALLOWED_WRITABLE_FILES`;
the membership check runs first). New `tests/test_git_ops_real_repo.py`
drives real git and reproduces the original error if the `-f` is
removed.

**What's left.** H1/H2 were decided and fixed on 2026-08-16 (operator
call: the loop writes only inside `autoresearch/`). H3, H4, H5 and the
packaging note remain, and each is still gated on an operator decision
that the audit could not make — §4 of the plan doc: branch/bench
retention policy, whether the Researcher ever gets a git ledger (the
never-created follow-up from
`sprints/2026-05-11-experiment-branches/SPRINT.md:30`), whether
`lab/data/experiments/` moves under the PKB root (clean-experience Gap 8,
still unfiled elsewhere), and whether "no sharing story" is the intended
end state for a local-first blueprint.

**The highest-value remaining item is not on this list, and now has its
own record: `docs/plans/test-suite-triage.md`.** H0 (the loop could not
commit at all) survived because every git seam was stubbed and nobody was
watching the red. Triage found 33 of 54 failures are cross-test
pollution — they pass standalone — and that no CI job runs the full
suite, which is the actual defect. The polluter is not yet identified;
the triage records the reproduction and what is ruled out.

**Also worth knowing.** The doc drift this audit found — README calling
the Researcher a code-writing committer, `agent-loop.md` saying `git
reset`, `tuning-loop.md` listing two whitelisted files — was fixed in the
same branch. The remaining prose risk is that "autoresearch" still names
two unrelated engines.

---

## Portal authentication

**Filed by:** `sprints/2026-09-20-buddy-front-and-center/ARCHITECTURE.md`
("The portal has no authentication — stated plainly").

**The gap.** `onboarding_gate` middleware (`app.py:395`) is not auth: it
blocks every surface only until a passphrase exists, then every request
passes with no per-request credential check — no `Depends(`, no token
compare, anywhere in `portal/app.py`. Default bind is `127.0.0.1`;
`BIND_ADDR=0.0.0.0` is supported and explicitly accepted as an
operator-opted-in exposure. `local_trust_boundary` gives DNS-rebinding and
cross-site protection for *mutating* methods only. **Any process running
as any user on the machine — and, on a widened bind, any host on the
LAN — can read every portal GET.**

**Why it wasn't done now.** The buddy-front-and-center sprint reduces the
*amount of sensitive content* behind that surface (flight-recorder bodies
off by default, redacted, capped, admin-gated) but does not add
authentication — a materially larger, cross-cutting change (session
tokens or equivalent, every route, every existing integration that assumes
no auth) that deserves its own VISION/ARCHITECTURE pass, not a rider on an
observability sprint.

**What a future sprint needs to decide:** session-based auth vs. a
bearer-token model; whether `LAB_MODE=hybrid` cloud-key flows need a
different trust boundary than local-only; how a forked/renamed lab
(`examples/peanut_farmer/`) inherits whatever the answer is without a
hardcoded assumption about the product name.

---

## Seventeen `/api/admin/*` endpoints are not tier-gated at the API layer

**Filed by:** `sprints/2026-09-20-buddy-front-and-center/BUILD_LOG.md`
(S5's deviation #1), discovered while wiring the sprint's own new admin
endpoints.

**The gap.** `ARCHITECTURE.md`'s contract #6 cites `/api/admin/security`
as existing precedent for "gated by `_require_surface('admin')`, which
404s on minimalist." That citation does not match the code: grepping every
`@app.get("/api/admin/...")` / `@app.post("/api/admin/...")` route in
`portal/app.py` (17 of them, predating this sprint — components,
check-updates, perf, cleanup, security, scheduler, models, ...) finds zero
calls to `_require_surface("admin")`. Only the `/admin` **page** route
itself is gated; its JSON API siblings are wide open regardless of tier.
A minimalist user who knows or guesses a URL can call any of them.

**Why it wasn't done now.** Out of this sprint's scope — this sprint's
job was to instrument the chokepoint and gate its *own* four new
endpoints correctly (done: `agent-lanes`, `agent-trace-stream`,
`agent-trace/{id}`, `agents/hold`, `flight-recorder`, plus the three
legacy-bodies endpoints), not to retrofit 17 unrelated, already-shipped
endpoints. Fixing it now would have been exactly the scope expansion the
sprint's own ledger warns against.

**What a future sprint needs to decide:** whether all 17 get
`_require_surface("admin")` in one pass (likely low-risk — they're
already conceptually admin-only, just not enforced) or whether some are
deliberately meant to be readable pre-tier-check for a reason not
currently documented; a regression test parameterised over the full
existing list (mirroring this sprint's own F13 pattern) would catch any
future admin endpoint shipped without the gate too.

---

## The agent-inference gateway — gated on this sprint's own overlap measurement

**Filed by:** `sprints/2026-09-20-buddy-front-and-center/VISION.md` (DE1),
cut from P1's scope per the ledger.

**The gap.** Agent calls never enter `inference_slot` — they run outside
the queue that serialises chat/world-forge/etc. work. The brief originally
asked for a single admission gateway that would also *order* agent calls
behind interactive chat. VISION.md's V8 finding showed the real blast
radius is ~11 sites (not ~5), 4 of them non-agent, 1 cross-process — the
riskiest refactor in the whole three-phase programme — and cut it in favor
of first building the read-only overlap counter this sprint actually
shipped (`slot.overlap_pct`/`slot.samples` in
`GET /api/admin/agent-lanes`, fed by `held_by_other` recorded at every
agent call).

**Why it wasn't done now.** Building the gateway before the
instrumentation that tells you whether it's needed inverts D18 (the whole
reason this sprint was ordered first). The overlap counter is that
instrumentation; it now exists.

**What a future sprint needs to decide, using DE1's pre-committed
thresholds** (from a week of the operator's ordinary use, once
`overlap_pct` has real samples): **< 5%** → defer the gateway indefinitely,
the contention risk was theoretical; **5-25%** → build it after P2's brain
bake-off, as originally planned; **> 25%** → promote it ahead of the
Buddy panel (P3) and revisit the deferral itself as having been wrong.

---

## Pre-existing, flagged not fixed by the buddy-front-and-center sprint

**Filed by:** `sprints/2026-09-20-buddy-front-and-center/ARCHITECTURE.md`
("Where the spec is wrong or unbuildable as scoped", items 7 and 8) —
found while reading the router/backend and cost-tracking code this sprint
touched, explicitly not fixed because each is a behaviour change on a path
this sprint does not otherwise touch.

- **`MLXBackend.stream_complete` signature bug** (`router/backends.py:
  330-332`): does not accept `system=`/`messages=`, but
  `ModelRouter.stream_complete` (`router/core.py`) always passes both →
  `TypeError` on any MLX streaming call through the router. Currently
  unreachable in production (chat resolves through the registry to
  Ollama/OpenAICompat), but this sprint's own S3 work
  (`ARAIL_AGENT_STREAM_FAST`) makes agent-path streaming a live feature
  for the first time — worth fixing before an MLX-backed agent path is
  ever wired to stream.
- **Concurrent `costs.json` write race**: the goal-parser child process
  runs its own `cost_tracker` singleton against the *same* `costs.json`
  as the parent, and `CostTracker._save()` is a non-atomic `write_text`.
  Parent and child can race. Not introduced by this sprint and not
  widened by it — `agent_trace.jsonl`'s append-only, one-writer-per-
  process design (contract #9) is strictly safer than what `costs.json`
  already does, which is why the trace store didn't repeat the mistake
  rather than a reason to leave `costs.json` unfixed forever.

**What a future sprint needs to decide:** whether the MLX signature bug
gets a defensive fix now (cheap: accept and ignore the two kwargs, or
route them through) or waits for an actual MLX-backed streaming caller to
surface it; whether `costs.json`'s write path moves to the same
temp-file-plus-`os.replace` pattern `agent_trace.py`/`activity.py`
already use.

---

## `/api/agents/status`'s deprecated `tokens` alias — removal window

**Filed by:** `sprints/2026-09-20-buddy-front-and-center/ARCHITECTURE.md`
(contract #8, the V7 fix) and `BUILD_LOG.md` (S6).

**The gap.** `GET /api/agents/status` now emits both `tokens_out` (new,
correct — real usage from the trace ring) and `tokens` (deprecated alias,
now carrying the *same corrected* value) because `agents.html` still reads
`.tokens` in four places. The alias is meant to live for **one release**,
not indefinitely.

**What a future sprint needs to do:** once `agents.html` is confirmed to
read `tokens_out` everywhere `tokens` was read (four `fmtTokens(...)`
call sites plus the Researcher meta-line, already switched to prefer
`tokens_out` this sprint but still falling back to `tokens`), drop the
`tokens` key from the endpoint response and the fallback reads in the
template.

---

## Buddy-front-and-center's BLOCK-review fix loop — filed as debt, not fixed

**Filed by:** `sprints/2026-09-20-buddy-front-and-center/REVIEW.md`
("Required actions before merge" → "File as debt (BACKLOG, not this
sprint)"), during the fix loop that resolved B1–B6, D6, D8, F9, F13, and
S1 (S1 was explicitly promoted to must-fix by the operator; the rest
below were not).

- **S2** — `src/arail/skills/goal_parser/__init__.py:250`:
  `error_class=str(payload.get("error", "unknown"))[:80]` persists up to 80
  chars of arbitrary child-process exception text to `agent_traces.jsonl`,
  outside `redact.capture_body`'s reach, regardless of the flight recorder.
  Everywhere else in this sprint `error_class` is `type(exc).__name__` only
  — the one field guaranteed to carry no payload. Fix is one line:
  `str(...).split(":", 1)[0][:64]`, or add a separate `error_detail` field
  that goes through `redact.redact()` and is only populated when the
  recorder is on.
- **S3** — Buddy's dream announcement (`_builtin_buddy.py`'s `dream()`)
  puts up to 160 chars of raw model output into `activity.jsonl` via
  `data={"preview": reflection[:160]}`, ungated by redaction and
  independent of the flight recorder. **This is now LIVE, not merely
  gated.** REVIEW.md R5 (re-review): the fix loop's own `dream()`
  NameError fix (adding the missing `from arail.activity import
  activity_log` import) was correct and in scope, but it activates a
  path that had never once run to completion in production — on
  pristine main, every call to `dream()` raised `NameError` before
  reaching this emit, and `dream_daemon._dream_once` caught that
  exception and logged a warn instead. After the fix loop, on an
  unheld lab, `dream()` reaches this line and writes the preview every
  night. The speech_gate added this same fix loop only silences it
  while **held** — an unheld lab (the default) now has this line firing
  for the first time ever. Not a `prompt_trace` body, so
  `_has_legacy_body`/the legacy-bodies purge will never find it.
  **QA must be told this directly, and must exercise the dream path
  specifically**: (1) a "grep the whole DATA_DIR tree" pass can
  legitimately hit a planted string here, and that is a real, now-live
  finding, not a false positive; (2) everything in `dream()` after the
  emit (`_recent_actions.append`, `_sync_workflow`, `return reflection`)
  is also newly-reachable code that has never executed in production
  and is therefore untested in practice, independent of this specific
  leak.
- **D1** — `ARAIL_AGENT_STREAM_FAST` removes the 120s total-generation
  ceiling from Buddy's fast streamed calls (a `requests` per-read socket
  timeout, not a total-duration timeout, once `stream=True`); undocumented.
  Needs a bound plus a doc note on the behaviour change.
- **D2** — no structural test asserts Ollama's streamed vs non-streamed
  request bodies agree on everything except `stream`; add one so a future
  edit to one path can't silently diverge from the other.
- **D3** — `lanes_snapshot()` returns `user_defined`, `dropped_writes`,
  `overlap_pct` that `admin.html` never renders; the trace drill-in
  (`GET /api/admin/agent-trace/{id}`) has no UI entry point; `SYS_LANES`
  is defined but neither emitted nor deleted.
- **D5** — `_builtin_presence.py:127`'s thread should use whatever
  `spawn_thread`/context-carrying helper the rest of the agents use (F3's
  static guard would have caught this); document in `docs/agents.md` and
  the Admin copy that a call which lost its attribution context is
  visible in the trace but **not** covered by Hold.
- **D7** — the admin SSE stream (`agent-trace-stream`) has no
  backpressure/debounce behaviour defined for a fast-emitting lane; decide
  how DE4's "kill line" number is measured against it.
- **F2** — a disk-rotation failure in `_append_disk` is silently dropped;
  should increment `dropped_writes` the same way a write failure does.
- **W1's wall-clock SSE test** — promote the "within 2s" structural guard
  (no `setInterval` near the lanes markup, pinned this fix loop) to an
  actual timed `@pytest.mark.timing` test, plus a stronger `q.qsize()`
  assertion on the subscriber queue than currently exists.
- **conftest split** — the shared fixture doing both hermeticity
  (DATA_DIR redirection) and ambient defaults (tier, etc.) should split
  into two, so a test that only needs one doesn't have to reason about
  the other.
- **D8's remainder** — the `/api/agents/status` fallback loop bug, the
  `calls_by_source` cardinality cap, streamed-response `model` provenance,
  a missing trace on an abandoned stream, the purged-event UI string, and
  registering `pytest.mark.perf` in `pyproject.toml` (currently an
  unregistered marker warning).

**What a future sprint needs to do:** pick these up in order of the
security/exposure gradient — S2 first (it is the only remaining path that
writes unredacted free text to disk regardless of the recorder), then S3
(no code fix required, but it is now a live leak, not a theoretical one
— see above), then D1 (undocumented timeout-semantics change, already
shipped), then the rest as capacity allows. QA must be briefed on S3
before the next sprint's QA pass (both that the leak is real and now
live, and that the dream path itself is newly-reachable and untested in
practice), or a planted-string hit there will keep looking like a false
positive.
