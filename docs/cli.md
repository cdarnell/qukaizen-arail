# `arailctl` CLI reference

> Canonical, hand-maintained reference for every `./arailctl <verb>`. This
> is the doc `tests/cli/verbs_driver.sh` checks against (a drift guard: every
> `case` arm in `arailctl` must appear somewhere below — F33 in
> `sprints/2026-07-29-elite-cli/ARCHITECTURE.md`). If you add or rename a
> verb, update this file in the same commit.
>
> Finalized at the end of the `2026-07-29-elite-cli` sprint (WP8), against
> the complete, shipped CLI — every verb, flag, and exit code below is
> real. `sprints/2026-07-29-elite-cli/ARCHITECTURE.md` is the design
> record (rationale, failure modes, the WP-by-WP build order); this file
> is the day-to-day reference.

## Cross-cutting conventions

| Control | Meaning |
|---|---|
| `NO_COLOR` (any value) | Disables ANSI color codes everywhere in the CLI |
| `ARAIL_COLOR=always\|never\|auto` | Overrides the tty test; `auto` (default) = colors iff stdout is a tty and `NO_COLOR` is unset |
| `! [[ -t 1 ]]` (stdout not a tty) | ANSI disabled; `setup`'s passphrase banner is masked |
| `! [[ -t 0 ]]` (stdin not a tty) | No verb prompts; `ARAIL_NONINTERACTIVE=1` has the same effect. `install`'s one guarded confirmation (`--rebuild-venv`) treats a non-tty stdin as `--yes` |
| `ARAIL_QUIET=1` | Same as `--quiet` for `setup` and `install` — masks the passphrase banner / suppresses decorative narration |
| `ARAIL_WARM_TIMEOUT_SEC` | Caps how long `start --warm` / `restart --warm` polls for warm-up completion (default `90`) |

### Exit-code contract

Codes are **additive only**: an existing non-zero code never gets
renumbered or reused for a different meaning (`sprints/2026-07-29-elite-cli/ARCHITECTURE.md` §12.1).

| Code | Meaning | Where it applies |
|---|---|---|
| `0` | Success / affirmative verdict | every verb |
| `1` | Failure or refusal — couldn't do the thing, or refuse to (no `.venv`, daemon active with `--world`/`--root`, claim held, instance ceiling, bind conflict, root portal never came up, multiple live instances with no `restart` target, lab not provisioned, lab live without `--allow-running`, deps refresh failed, doctor broken) | `start`, `restart`, `stop`, `install`, `doctor` |
| `2` | Usage error — bad flag, missing flag value, invalid slug, ambiguous non-interactive target, unknown `--json` value, `install daemon` (typo) | every verb with flags |
| `3` | **Degraded** — partially up, a phase refused/failed but the lab is still usable, `--check` pending changes | `status`, `doctor`, `install`, `update` |
| `4` | **Nothing running** | `status` only |
| `130` / `143` | Killed by SIGINT / SIGTERM | `start --world <slug>` (foreground instance/root path) |

`stop` deliberately always exits `0` when there was nothing to stop — a
stop verb's contract is the post-condition ("it is down now"), and
`restart` chains on it.

## Verbs

### `setup`

First-time provisioning: platform packages, `.venv`, `.env`/`lab.conf`,
ports, PKB, default model, PATH shim, verify. Idempotent — respects an
existing `.env`. The **only** verb that may prompt, install OS packages,
or create/rewrite `.env`/`lab.conf`.

**The relational store (`arail.db`).** After deps and the lab tree are
in place, `setup` runs the same `ensure_db(apply=True)` sweep `install`
uses (`sprints/2026-08-10-arail2-persistence-instantiated`
ARCHITECTURE.md §10) over every root `resolve_data_dirs()` resolves —
closing the one gap between `setup` and `start` where `./arailctl
doctor` would otherwise honestly report the relational store as not yet
instantiated. SAFE-FORWARD only, same as `install`/`start`; never fails
`setup` — a blocked/ahead/diverged root warns and setup continues.

| Flag | Effect |
|---|---|
| `--with-coder` / `--no-coder` | Download the Qwen2.5-Coder starter model, or don't |
| `--yes` / `-y` | Same as `ARAIL_NONINTERACTIVE=1` — never prompts, default-yes everything |
| `--quiet` | Masks the passphrase in the end-of-setup banner (also: `ARAIL_QUIET=1`, or automatically whenever stdout isn't a tty) |

Exit: `0` success · `1` any `error()` (e.g. no Python 3.10+ and install
declined) · `2` unknown flag.

### `install`

Refresh an already-provisioned lab: `source` → `deps` → `components` →
`models` → `verify`, in that order. Requires a provisioned lab (refuses
with the exact `setup` command otherwise) and requires the lab to be
**stopped** (refuses with `./arailctl stop`, naming what's live, unless
`--allow-running`). Non-destructive by default: no model download without
`--models`, no `.venv` deletion without `--rebuild-venv`, no `git`
mutation beyond `pull --ff-only` on a clean, attached, tracking branch
(never `stash`/`reset`/`clean`/`merge`/`rebase`). Honors
`LAB_MODE=airgapped` on every network-touching phase (`source`, `deps`,
`components`, and `models`' `--models` apply step) — refuses with a named
reason unless `--force`.

| Flag | Effect |
|---|---|
| `--check` / `--dry-run` | Detect-only: reports what would change, mutates nothing |
| `--only <phase>[,<phase>]` | Run only the named phases (`source`,`deps`,`components`,`models`,`verify`) |
| `--skip <phase>[,<phase>]` | Run every phase except the named ones (mutually exclusive with `--only`) |
| `--models` | Actually apply the detected model drift fix (default: report only, per the metered-link doctrine) |
| `--rebuild-venv` | Delete and recreate `.venv` before `pip install`; asks for confirmation on a tty unless `--yes` |
| `--allow-running` | Proceed even though the lab is currently live (normally refused, exit 1) |
| `--force` | Override the `LAB_MODE=airgapped` refusal on network-touching phases |
| `--yes` / `-y` | Non-interactive is implied already; this only affects the `--rebuild-venv` confirmation on a tty |
| `--quiet` | Suppress decorative narration (also: `ARAIL_QUIET=1`) |
| `--json` | Emit `arail.install/v1` on stdout only; all narration moves to stderr |
| `-h` / `--help` | Usage |

**Phases:**

| Phase | Does | Refuses (→ degraded, remaining phases still run) |
|---|---|---|
| `[1/5] source` | `git pull --ff-only`; prints `old…new` short SHAs + commit count; **re-execs itself if HEAD moved** so a self-update never runs on stale bytes | not a git repo · dirty worktree · detached HEAD · no upstream · diverged (not a fast-forward) · airgapped without `--force` |
| `[2/5] deps` | `pip install -e ".[$LAB_TIER]"` (idempotent); with `--rebuild-venv`, recreates `.venv` first (**failure here is a hard failure, exit 1** — not degraded) | airgapped without `--force` |
| `[3/5] components` | `scripts/update.sh --apply --non-interactive` — `components.json` drives it; a single component's failure warns and continues | airgapped without `--force` (`update.sh`'s own check) |
| `[4/5] models` | Compares the expected primary chat model (`model_defaults.yaml`'s `default_a` if present, else `llama-ai-eng`) against `ollama list`; reports drift + the exact commands; applies only with `--models` | ollama absent / daemon unreachable → skipped (never hangs); the apply step also honors the airgap refusal |
| `[5/5] verify` | `./arailctl doctor` — its exit code folds straight through (`3`→degraded, `1`→hard failure); before that, ensures every resolved root's `arail.db` is created and forward-migrated (see "The relational store" below) | — |

**The relational store (`arail.db`).** Before `[5/5] verify`, `install`
runs `ensure_db(apply=True)` over every root `resolve_data_dirs()`
resolves — root lab, every registered instance, and every on-disk
instance with no registry record — printing one `[db]` line per root
that actually acted (silent for a root that's already `ok`). This is the
"seamless" promise's real home (sprints/2026-08-10-arail2-persistence-
instantiated §4.6): after `install`, every lab on the machine has a
database. SAFE-FORWARD migrations only — additive `CREATE TABLE`/`CREATE
INDEX`/`ALTER TABLE … ADD COLUMN`, never a statement that can remove or
rewrite a row. Anything LOSSY/AHEAD/DIVERGED is reported, never applied
— `install` degrades (not hard-fails) and names the exact verb
(`./arailctl db apply --allow-destructive`, `./arailctl db plan`, or "update
this checkout"). Note: `arail.db` has **no runtime reader yet** as of this
sprint — creating it satisfies "the dependent service is up," it does not
by itself make any feature work.

Exit: `0` all phases ok/no-op · `3` degraded (a phase refused or failed,
lab still usable; also `--check` pending changes) · `1`
hard failure (deps refresh failed, not provisioned, lab live without
`--allow-running`, verify broken) · `2` bad flags (including
`install daemon`, a typo for `install-daemon` — hinted, not run).

### `update` (alias for `install`)

Permanent alias — no removal date (ARAIL is a forked blueprint; removing
a verb breaks other people's scripts silently). Prints a one-line notice
on **stderr** (`update --json | jq` still works — stdout stays clean) and
runs exactly what `install` would with the same flags. The one carve-out:
**`update --component <name>`** is forwarded verbatim to the old,
interactive `scripts/update.sh` path (unchanged behavior, no notice —
nothing about it is aliased). `update --check` behaves exactly like
`install --check` (exit `3` when changes are pending; previously always
exited `0`, an intentional behavior change).

### `tier [<minimalist|maximus>]`

The canonical name for the feature-set axis. No argument prints the
current tier plus the two switch commands and exits `0`. With an
argument, delegates to `scripts/upgrade.sh` unchanged — `pip install -e
".[<tier>]"`, writes `LAB_TIER` to `.env`. Downgrading doesn't uninstall
anything; the extra tabs just hide until you upgrade again.

| Flag | Effect |
|---|---|
| `--with-coder` / `--no-coder` | Also (or don't) download the Qwen2.5-Coder starter model |

Exit: `0` success (or bare `tier` printing the current one) · `1`
pip/tier failure · `2` unknown tier.

### `upgrade` (alias for `tier`)

Permanent alias — same no-removal-date rationale as `update`/`install`.
Prints a one-line notice on stderr, then behaves exactly like `tier`.
`upgrade` with no argument used to `die "usage: ..."` (exit `1`); it now
prints the current tier and exits `0`, matching `tier`'s own behavior —
an intentional, documented change.

### `version`

Print installed component versions (`scripts/update.sh --version-only`).
Unchanged.

### `start`

Launch the lab: foreground `scripts/start.sh`, or (if `install-daemon` has
been run and launchd is actively supervising) drive `launchctl` instead —
with a real readiness gate either way, not a print-and-hope.

| Flag | Effect |
|---|---|
| `--world <slug>` / `--world=<slug>` | Start (or attach to) one Concurrent-Worlds instance |
| `--root` | Start the root lab explicitly, even with Worlds configured. Mutually exclusive with `--world` (exit 2 if both given). **Not** the same thing as `--world root` — a World can validly be named `root`; the picker names the distinction when that's the case |
| `--port <n>` | Override the allocated/default portal port |
| `--no-browser` | Suppress auto-opening the dashboard |
| `--list` | Print configured Worlds and exit (side-effect free) |
| `--pick` | Force the interactive picker even with fewer than 2 Worlds configured (which would otherwise auto-select the single World, leaving no way to reach the root lab but `--root`). Mutually exclusive with `--world`, `--root`, and `--yes` (exit 2). Needs a tty — exit 2 without one. This is what `switch` uses |
| `--yes` | Take the picker's default — the lab you launched last, or the root lab if this checkout has never launched one — without asking, and print which. Works with or without a tty (its whole audience is scripts). Mutually exclusive with `--pick` |
| `--warm` | After the lab is up, poll for the boot-time model warm-up to finish and print one honest line (`✓ via <backend> in N.Ns` / `⚠ not complete within Ns` / `— not applicable for backend <name>`). Never gates readiness, never changes the exit code. Daemon mode prints a hint instead of polling (its env is fixed by the launchd plist) |
| `-h` / `--help` | Usage |

`--world`/`--root`/`--list`/`--help` always reach `scripts/start.sh`
directly, even when a daemon is active — daemon-mode refusal is
`start.sh`'s own job so it can name the World slug (or `--root`) in the
message.

**Model selection, settled once and read-only here.** The readiness
banner always prints a `Models:` block — the two boot slots (A: the
resident chat model; B: the model AeroLLM references for deep answers),
each with size, on-filesystem presence, and RAM fit, plus the exact
`ollama pull` / `hf download` command when a configured model is
missing. `start` never picks a model itself and never prompts — the
interactive two-slot picker lives in the portal (a banner under the
statusbar on every page until you confirm once, then only reappearing if
something breaks — missing, doesn't fit, or `.env` drifted away from
what was settled). Both surfaces read the same source of truth:
`model_defaults.yaml` (repo root, gitignored — see
`model_defaults.yaml.example`), written by the portal's
`POST /api/models/settle` and read back with
`python -m arail.model_defaults --banner` (what `start` shells out to)
or `--get default_a`/`--get default_b` (one resolved value, empty line
if unset — used to fix the Ollama-presence check below instead of
hardcoding `llama-ai-eng`). Deleting `model_defaults.yaml` re-arms the
portal picker on the next page load.

**The relational store (`arail.db`), a dependent service** (sprints/
2026-08-10-arail2-persistence-instantiated §4.5). Before the portal
binds, `start` ensures THIS instance's own `arail.db` — never a
sibling's, and never any root-relative path a `--world` invocation
shouldn't touch — is created and forward-migrated: `ensure_db(this_data_dir,
apply=True)`. SAFE-FORWARD only, and reported when it acts: `db: created
lab/instances/ai/data/arail.db (schema v1)`. A healthy, already-`ok` DB
prints nothing (quiet boot). A LOSSY/AHEAD/DIVERGED/unavailable DB
**warns, names the exact fixing verb, and `start` continues booting** —
this is deliberate, not a bug: nothing outside `src/arail/dbspec/` reads
`arail.db` at runtime yet (verified by grep), so refusing to start a
working lab over an inert store would trade a real outage for a
theoretical one. **This is revisitable**: the moment a runtime feature
starts reading `arail.db`, this must become a hard readiness gate — filed
in `sprints/BACKLOG.md`.

**Root-lab readiness:** a pre-spawn check refuses immediately (before
anything is started) if the portal port is already answering
(`Root lab already running — try: ./arailctl status`). After every
service is spawned, each is polled: **Portal is required** — if it never
answers `/api/instance` with HTTP 200 from *this* checkout, every process
this invocation spawned is stopped and `start` exits `1`. Memory, MLX
(when `MODEL_BACKEND=mlx`), Terminal, Notebook, and IDE are
**degrade-only** — a missing one prints `⚠` and is left out of the final
URL block, but never aborts the launch. The closing banner says
`All services running.` only when nothing degraded; otherwise
`Lab running — degraded: <services>.` Daemon mode gets the identical
honesty: after `launchctl kickstart`, `start` polls `/api/instance` for
up to 30s before reporting success; if the portal never answers, `start`
exits `1` naming `lab/logs/portal.err.log` instead of printing an
unverified URL.

**The picker.** With 2+ Worlds configured and no target given, an
interactive tty picker appears. Option `0` is always the root lab
(non-interactively `--root`) — and when a World is *mounted* into the root
lab, option `0` names it (`Autoresearch AI Lab — Debt Finance World
mounted (:8080)`), because otherwise that World and its own catalog row
are two indistinguishable names for two different labs. Each World row
shows its liveness (`● running :8090` / `○ not running`).

Non-interactive with no target still exits `2`, listing every
`./arailctl start --world <slug>` command plus `--root` — VISION §3's
"never guess" ruling is what CI, daemons, and scripted callers depend on,
and no memory overrides it. A running World/root lab is attached to (URL
printed, browser opened), never respawned.

**The picker remembers.** Every *successful* start — root lab, World
instance, or an attach to one already running — records its target in
`lab/instances/last-target.json` (schema `arail.last-target/v1`). The next
picker marks that row `← last` and makes it the Enter-default, so pressing
Enter returns you to the lab you were last in. A checkout that has never
launched anything defaults to option `0`. The write happens only *after*
readiness passes, so a World that crash-loops on boot never becomes the
sticky default; a remembered World later deleted from the catalog degrades
to option `0` with one line saying so. The file is a preference, not
state: corrupt, empty, or hand-edited-hostile content is treated as
absent, and a failure to write it can never fail a start.

With exactly one World configured the picker still does not appear
(VISION §3 — it must not tax the single-World user), but the memory is
honored: if you last ran the root lab, bare `start` returns you there and
says so. `--pick` forces the prompt in either case.

Exit: `0` ready (or attached) · `1` failure/refusal (no `.venv`, daemon
active with `--world`/`--root`, claim held, instance ceiling, bind
conflict, root portal never came up, daemon kickstart never answered
within 30s) · `2` usage (bad flag, unknown slug, `--root` with `--world`,
ambiguous picker non-interactively) · `130`/`143` SIGINT/SIGTERM
(foreground instance/root path).

### `stop`

Stop lab services (unloads launchd agents in daemon mode).

| Flag | Effect |
|---|---|
| `--world <slug>` | Stop one World instance |
| `--root` | Stop only the root lab's services — never touches a live World instance, even while one is running |
| `--all` | Stop every configured World instance, then the root lab |

Exit: `0` always (including "nothing was running" — a stop verb's
contract is the post-condition) · `1` multiple live instances with no
target (bare `stop`, neither flag given) / instance support unavailable ·
`2` invalid slug, unknown/malformed flag (including a value-less
`--world`) — a usage mistake is always reported as one and never
silently widens the stop's scope.

### `restart`

Stop then start — **scoped to exactly one target**, never a sibling
World (the bug this sprint's redesign retires: an unscoped stop used to
be able to kill a live sibling instance mid-write). Forwards every flag
`start` accepts, including `--warm`; never re-parses them itself (a
second parser is how the original scoping bug happened).

| Target resolution (no flags reparsed — read-only argv scan + registry read) | Behavior |
|---|---|
| `--world <slug>` given | Stops exactly that World, then starts it |
| `--root` given | Stops only the root lab's services, then starts the root lab |
| Neither; daemon active | `launchctl kickstart -k` + the same readiness gate `start`'s daemon branch uses |
| Neither; exactly 1 live instance | Stops and restarts that instance |
| Neither; 0 live instances | Stops the root lab, then `start` does its own resolution/picker |
| Neither; ≥2 live instances | **Exit 2** — lists `restart --world <slug>` for each, plus `--root` |
| `--all` | **Exit 2** — explicit refusal with an explanation (a foreground start hosts exactly one target; multi-instance restart needs a supervisor this sprint does not build). Use `stop --all`, then start each in its own terminal |
| `--world`/`--root` while a daemon is active | **Exit 1** — refuses by name rather than silently kickstarting the root daemon instead of the thing actually asked for |

If the scoped stop phase fails, the start is never attempted (exit `1`).
If the stop succeeds but the subsequent start fails, `restart` prints
`'<target>' was stopped, and the start failed (above) — the lab is now
DOWN.` before propagating the start's own exit code — a silent "start
failed" after a successful stop is how an operator ends up with a down
lab and no idea why.

Exit: inherited from the `start` it drives (`0`/`1`/`2`/`130`/`143`),
plus its own `2` (ambiguous target, `--all`) and `1` (scoped stop
failed).

### `switch`

Jump from one World to another in one command: stop whatever is running,
then pick. This is the verb for the way most operators actually use
Worlds — one at a time, switching between them — which `restart` cannot
be, because `restart` deliberately pins to the *current* target.

```bash
./arailctl switch                  # stop what's live, then the picker
./arailctl switch --world finance  # …or go straight there, no prompt
./arailctl switch --root           # …or back to the root lab
```

| Target | Behavior |
|---|---|
| No flags | Stops **every** live World instance, then the root lab, then hands off to `start.sh --pick` — the picker, forced even with a single World configured |
| `--world <slug>` | Same scoped stop, then `start --world <slug>`; no prompt |
| `--root` | Same scoped stop, then `start --root`; no prompt |
| `--root` with `--world` | **Exit 2** — usage error |
| Daemon active | **Exit 1** — daemon mode is single-instance, so there is nothing to switch between; names `uninstall-daemon` as the fix |

The deliberate difference from `restart`: with ≥2 live instances `restart`
exits `2` (it cannot know which one you meant), whereas `switch` stops all
of them and then asks — collapsing to exactly one lab *is* what switching
means. Each stop is scoped (`reset.sh stop --world <slug>`), never an
unscoped stop that could take out a sibling mid-write.

`switch` owns no picker of its own — it forces `start.sh`'s. The lab you
land in is recorded the same way any other successful start is, so the
next bare `start` returns you there.

If a stop fails, the start is never attempted (exit `1`). If a stop
succeeds and the start then fails, `switch` prints `the previous lab was
stopped, and the start failed (above) — the lab is now DOWN.` before
propagating the start's exit code — the same F13 discipline `restart` uses,
for the same reason.

Exit: inherited from the `start` it drives (`0`/`1`/`2`/`130`/`143`), plus
its own `2` (`--root` with `--world`) and `1` (scoped stop failed, daemon
active).

### `deep <op>`

AeroLLM 2nd-inference control: `install` (bundled prebuilt binary) |
`rebuild` (from source) | `update` (published wheel) | `status`.

Three install channels, dispatched by `scripts/build-aerollm.sh auto`
(what `setup.sh` and a bare `./arailctl deep install` with no forced
channel both use):

| Channel | Op | Requires | Who it's for |
|---|---|---|---|
| BUNDLED | `deep install` | network to `github.com` (or a local tarball via `AEROLLM_BUNDLE_FILE`) | outside users — no source repo, no credentials |
| DEV | `deep rebuild` | the aeroLLM sibling repo checked out at `$ARAIL_AEROLLM_REPO` | maintainers with local aeroLLM changes |
| RELEASE | `deep update` | `pypi.qukaizen.com` index credentials | maintainers on the private index |

`auto` picks DEV if the sibling repo is present, RELEASE if
`AEROLLM_CHANNEL=release` or index credentials are configured, and
BUNDLED otherwise. Force any channel with `AEROLLM_CHANNEL=dev|release|bundle`
regardless of what's on disk.

**Bundled-channel env knobs** (all optional):

| Var | Default | Meaning |
|---|---|---|
| `AEROLLM_CHANNEL` | unset (auto) | force `dev` \| `release` \| `bundle` |
| `AEROLLM_BUNDLE_URL` | derived from repo/tag below | full asset URL override (mirrors, forks) |
| `AEROLLM_BUNDLE_REPO` | `cdarnell/qukaizen-arail` | which GitHub repo carries the release asset |
| `AEROLLM_BUNDLE_TAG` | pinned in `pyproject.toml` `[tool.arail.package-sources] aerollm_bundle_tag` | which ARAIL release carries the bundle |
| `AEROLLM_BUNDLE_FILE` | unset | use a local tarball instead of downloading — the offline / airgapped install path |
| `AEROLLM_BUNDLE_SHA256` | read from `pyproject.toml [tool.arail.package-sources] aerollm_bundle_sha256` — a genuine out-of-band pin, independent of the download — on both routes: `./arailctl setup` forwards it, and standalone `./arailctl deep install` reads pyproject itself when the env var is unset. An explicit env value always wins. Only if pyproject can't be read at all does it fall back to the same-origin `.sha256` sidecar | pin/override the expected **tarball** digest |

`./arailctl deep status` reports a `channel:` line (`dev` \| `release` \|
`bundled` \| `none`) and, when bundled, an `aerollm <version> (<short-sha>,
built <date>)` provenance line read from `aerollm_api.bundle.json`.

**What the sha256 check does and doesn't guarantee (v1):** when
`AEROLLM_BUNDLE_SHA256` is unset, the expected digest is fetched from the
same GitHub Release, over the same connection, as the tarball itself
(`<url>.sha256`). That catches corruption in transit — it is **not** an
authenticity or supply-chain-integrity control, since anyone able to serve
a malicious tarball from that origin can serve a matching digest alongside
it. The actual trust boundary is GitHub's TLS + repo access control, not
the checksum. If you need a digest independent of that origin, pass
`AEROLLM_BUNDLE_SHA256` explicitly from a value pinned in git — the
committed `pyproject.toml`'s `[tool.arail.package-sources]
aerollm_bundle_sha256` (the TARBALL digest; this is what `./arailctl
setup` forwards automatically at tier `maximus`). **Do not** pass
`THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`'s `sha256` field here — that
field is a digest of the extracted `aerollm_api.abi3.so` itself, not the
tarball, and is verified separately (against the tarball's own
`MANIFEST.json`, after extraction) — the two digests are of different
objects and will never match `AEROLLM_BUNDLE_SHA256`.

**Beyond the tarball checksum:** after extraction, `bundle_install()` also
(a) verifies the extracted `.so`'s sha256 against the in-tarball
`MANIFEST.json` (catches a tarball-vs-member substitution) and (b) checks
the `.so` has Mach-O 64-bit magic bytes before copying it into
site-packages (catches an obviously-wrong payload). Neither check is a
codesign, notarization, or authenticity control — `aerollm_api.abi3.so` is
an **unsigned** binary, and importing a native extension is always code
execution at import time. The full trust chain here is same-origin: GitHub
TLS + repo access control, one hop. Treat the bundled channel accordingly.

See `sprints/2026-08-05-arail-bundled-aerollm/ARCHITECTURE.md` for the
full design and `docs/releasing.md` for the maintainer-side bundle
refresh checklist.

### `reset [mode]`

Wipe state: `models`\|`data`\|`pkb`\|`plugins`\|`env`\|`full`\|`destroy`.
See `./arailctl reset help`.

`full` wipes models, data, plugins, the research program, and
`.venv`/`.env`/`lab.conf` — everything except the knowledge base (`pkb`;
chain with `reset pkb` if you want that gone too) and source code.
**Downloaded models are the one exception inside `full`**: `lab/models/`
is kept unless you say otherwise — `--include-models` on the command
line, or a yes at the interactive prompt `full` asks when run without
`--yes`. A bare `--yes` confirms the wipe as a whole; it does not by
itself imply "and delete my downloaded models too," since re-fetching
them is the one step with a real time/bandwidth cost to undo.

### `status`

One collector, one `arail.status/v2` document, two renderers (the human
table and `--json`) — they can never disagree. HTTP/port probes
(`scripts/lib/services.sh`), not `pgrep` patterns, are the verdict
source; `pgrep` survives only as an "owner" hint, run only after a port
is already known to be listening.

| Flag | Effect |
|---|---|
| `--json` / `--json=full` | The whole `arail.status/v2` document |
| `--json=instances` | The bare instance-registry rows array — byte-compatible with the pre-sprint `--json` output (the documented stable form for scripts; see `docs/concurrent-worlds.md`) |
| `--probe` | Extended: adds a `/health` check for memory/MLX and the per-instance token+checkout mismatch probe |
| `--no-probe` | Zero HTTP calls — registry + port-listen only, fully deterministic (CI mode) |
| `--quiet` / `-q` | Suppress the top banner and the `Runtime state` (`du`) section |
| `--no-sizes` | Skip the `lab/` disk-usage walk (never in the JSON regardless — a `du` walk can cost real seconds on a large PKB, outside the <2s budget) |

Renders, in order: the World-instance table (`live`/`stale`/`unreadable`,
data-root-missing warnings), `.venv`/supervision status, the root lab
(one line — `root lab: not started (a World instance is running
instead)` — when a World is up and the root lab never was, replacing five
previously-dim "not running" rows; per-service `✓`/`⚠` lines when the
root lab IS up, with a URL printed **only** for a service that actually
answered), a foreign-checkout warning if a different process answers the
portal port, external Ollama reachability, then (human mode only) the
scheduler window and `lab/` disk usage. Stale registry records are pruned
**after** rendering, in every mode — a status command that silently
deletes what it just reported would be surprising.

**The relational store (`arail.db`) as a dependent service**
(sprints/2026-08-10-arail2-persistence-instantiated §4.4). Every
resolved root — root lab, every `registry.d/*.json` slug, and every
on-disk `lab/instances/<slug>/` dir with no registry record — gets a
read-only check (never creates what it checks; `apply=False`, always,
even without `--no-probe`; a local file read, budgeted <50ms/root). In
`--json`/`--json=full` this is additive:

```json
"root": {"state": "up", ..., "origin": "root",
         "db": {"state": "ok", "version": 1, "spec_version": 1, "present": true, "detail": ""}},
"instances": [{"slug": "ai", ..., "origin": "registry", "db": {...}}]
```

An on-disk-unregistered instance appears as its own synthetic row
(`"state": "unregistered"`, `"origin": "ondisk"`) — the "2 of 6 roots
reached" miss, made visible instead of silently skipped. **`--json=instances`
is unchanged** — it keeps emitting exactly the pre-existing bare rows
array, with neither `db` nor `origin` on any row; the augmentation only
ever lands in `--json`/`--json=full`'s `.instances`. Human mode prints a
`db:` line per lab only when that lab is up/live AND its db state isn't
already `ok`/`created`/`updated` — quiet when everything is fine, exactly
like every other line on this surface.

**When a live instance's data root itself is missing** (the pre-existing
`data_root_missing` warning), its `db` object is suppressed (`null`)
rather than reporting the derived state (which would always be
`pending` — there's nothing there to have a database) — that would be a
second, misleading complaint about the same underlying fact, naming the
wrong subsystem. This is a documented, deliberate exception: a live
instance with a missing data root still does **not** degrade `status`'s
exit code on its own (unchanged, pre-existing behavior) — the missing
root is reported, once, as a warning, not amplified into a second
`db:`-prefixed verdict reason.

Exit: `0` something expected is up and nothing is wrong · `3` degraded
(up but a service is down, a registry record is stale/unreadable, a
foreign checkout answers the portal port, **or a live root/instance's db
is `pending`/`blocked`/`ahead`/`diverged`**) · `4` nothing running (a
never-created `arail.db` on a lab that was never started does **not**
promote this to `3` — a lab that was never started is not degraded) · `1`
internal failure (registry directory unreadable) · `2` bad flag.

### `doctor`

Explicit environment checkup — `.venv`/import/`uvicorn` preflight plus
`python -m arail.doctor` (models, KB index, components, egress).

| Flag | Effect |
|---|---|
| `--updates` | Also run the remote component-update check (hybrid mode only) |
| `--strict` | Promote optional/info findings (a missing optional binary, no model installed) to degraded |

**Findings tally:** `uvicorn` absent, the egress guard failing to
install, or the PKB root being unwritable are **required** checks — any
of them failing degrades the run. `ttyd`/`jupyter`/`code-server` and "no
model installed" are **info** — reported, but only degrade the exit code
under `--strict`. Deliberate: CI (`.github/workflows/blueprint-smoke.yml`)
runs plain `./arailctl doctor` on a runner that legitimately lacks
`ttyd`, `code-server`, and any pulled model, and must keep exiting `0`.

Exit: `0` healthy · `1` broken (no `.venv`, `import arail` fails) · `3`
degraded (a required check failed, or `--strict` promoted an info one).

### `logs [component] [lines]`

Tail `lab/data/activity.jsonl`, optionally filtered by component
(`browser`\|`system`\|`researcher`\|`goal`\|`wiki`\|`pkb`\|`chat`\|`consent`).
Unchanged.

### `db <op>`

Declarative persistence. The spec tree in `spec/` is the source of truth;
SQLite, the LanceDB tables, and the generated resolver/registry are all
compiled from it. Never hand-edit the database or the generated files —
edit the spec and re-apply.

| Op | What it does | Exit |
|----|--------------|------|
| `plan` | Diff spec vs actual across both stores. No writes. | `0` |
| `apply` | Generate + lint a migration, `atlas schema apply`, reconcile the vector tables, regenerate code, record the spec version. | `0`, `1` on lint failure |
| `doctor` | Integrity checks, reported **per user**. | `0` clean, `3` on errors |
| `optimize` | Compact Lance tables and prune retained versions. | `0` |
| `drift` | CI gate — does actual match the spec? | `0` in sync, `3` on drift |
| `migrate` | One-shot 1.x → 2.0 data migration. | `0` |

Common options: `--data-dir DIR`, `--pkb-root DIR`, `--spec-dir DIR`
(accepted before or after the op). `apply` takes `--allow-destructive` for
vector rebuilds; `migrate` takes `--lab-root DIR`, `--user NAME`, `--apply`.

**`plan`, `drift`, and `migrate` are dry-run by default** — `migrate` writes
nothing without `--apply`, and never modifies or deletes the 1.x source data.

Destructive vector changes (dimension, distance metric, index type) require a
rebuild and are **refused** unless `--allow-destructive` is passed. Safe
changes (add/drop/retype column, create/drop index) auto-apply.

`doctor` reports per user so single-user corruption is visible rather than
averaged away. It checks orphaned `content_refs`, embedding model/dimension
drift against the spec, degenerate vectors, fragment counts, retained
versions, missing vector indexes, and worlds with no entities.

> **Note on migration lint.** Since Atlas v0.38 `atlas migrate lint` requires
> an Atlas Pro login and exits non-zero *without linting*. When that happens
> `apply` runs a narrower local destructive-statement gate instead and says so
> in its output — a gate that did not run is never reported as a gate that
> passed. Run `atlas login` to get full lint with no code change.

### `pkb <op>`

`ingest`\|`compile`\|`browse` — ARAIL lab knowledge-base content ops.
Unchanged.

`prune` — reconcile the **Compiled KB** (the approved layer agents build
on) with what is actually on disk: drop approvals whose raw file no longer
exists.

| Flag | Effect |
|---|---|
| `--dry-run` | List what would be dropped, change nothing |

Why this exists: the Compiled KB is a *manifest of pointers* into the raw
corpus, not a second copy. Mounting a World runs
`world_mount._sweep_other_worlds()`, which deletes the previous World's
staged `sources/world-<slug>/` term files — deliberately, because a World
IS the lab's dataset. The approvals for those terms used to outlive their
files, and since the retrieval gate is a query-time intersection (approved
paths ∩ live search hits), a dangling pointer matches nothing. Enough of
them and `search_for_agents()` returns zero hits for **every** query in
**every** World, with no error raised anywhere — the gate fails closed, and
"no approved truth" is indistinguishable from "nothing approved yet."

Mount, swap, and `unmount --remove-staged` now prune automatically. This
verb is the manual door for a lab that already drifted. `./arailctl doctor`
reports the dangling count (and warns when a World is mounted but nothing
is approved, so agents would find nothing).

Exit: `0` pruned or already clean · `3` pkb root missing/unreadable — the
prune deliberately refuses there rather than treating "every path looks
deleted" as 556 revocations.

`bootstrap` — backfill the Compiled KB for one root: auto-approve the
currently-staged World's term pages, or write an empty-but-present manifest
if none qualify. Fixes the QA-6 symptom (agents get zero knowledge-base
results because the gate ships on while nothing has ever been approved).
`./arailctl install` (alias `update`) calls this once, non-fatally, for the
root lab. It is deliberately **not** called by `./arailctl start` — booting
must stay quiet and must never silently re-approve a term the operator
revoked. `world_mount.mount()` **and** `world_mount.swap()` both call the
same reconciliation on every mount/switch going forward — `bootstrap` exists
for backfilling roots that were mounted/swapped before this mechanism
shipped, or whose auto-approval was skipped by an escape hatch at the time.

| Flag | Effect |
|---|---|
| `--dry-run` | Print what would be approved, change nothing |
| `--world <slug>` | Target one World instance's PKB root (`lab/instances/<slug>/pkb`) instead of the root lab. `--world root` targets the root lab explicitly. |
| `--all-instances` | Run bootstrap for the root lab **and** every registered World instance (`lab/instances/<slug>/pkb` for each slug in the instance registry — see `scripts/lib/instances.sh`), one after another. Mutually exclusive with `--world`. Per-root failures are printed and do not abort the remaining roots; the verb's own exit code is non-zero if any root failed. |

A concurrent-Worlds lab (`./arailctl start --world <slug>`, see
`docs/concurrent-worlds.md`) has one PKB root per instance plus the root
lab's own — `./arailctl install` only ever backfills the root lab, so after
adding or resuming World instances, run `./arailctl pkb bootstrap
--all-instances` once to backfill every root in one command. This is the
full multi-World backfill procedure; there is no other documented path and
none is needed — hand-reconstructing an instance's env pack to invoke the
single-root form is not required.

Scope invariant (a security boundary, not a convenience default): a path is
auto-approved iff it matches `sources/world-<slug>/terms/<term-slug>.md`
**and** `<term-slug>` is present in the bundle's `terms.json`. `notes/`,
`inbox/`, `conversations/`, and everything under `agents/` are unreachable by
this mechanism no matter what a World bundle contains — only World-forged,
DaC-compiled term pages qualify.

Two verification levels, both auditable from `approved_by` in
`compiled/kb/approved.json`:

- `world-seal:<sha12>` — written by the `mount()`/`swap()` hook, where
  `<sha12>` is the first 12 hex chars of the bundle's `verify_seal()`-checked
  `computed_sha256`. The seal was actually verified before this approval was
  written.
- `world-terms:<sha12>` — written by `./arailctl pkb bootstrap`, where
  `<sha12>` is the sha256 of the catalog's `terms.json` bytes alone.
  `bootstrap()` reads the catalog copy directly and does **not** call
  `verify_seal()` — the stamp says so honestly rather than claiming a
  verification that did not happen. This is not a widened attack surface:
  anyone with write access to the lab who could forge a bundle that passes
  this check already has write access to `approved.json` itself.

Two escape hatches, both fail toward *less* auto-approval:

- `ARAIL_AUTO_APPROVE_WORLD_TERMS=off` disables the mount-time hook globally
  (the explicit `bootstrap` verb still works).
- A sentinel file `compiled/kb/no-auto-approve` under a PKB root disables it
  for that root only — presence, or any error reading it, counts as
  disabled. It's a file rather than a config flag so it lives with the data
  and survives a re-mount; drop it into a World's PKB root to opt that World
  out with no code change.

A term the operator explicitly revokes (`./arailctl pkb prune` doesn't do
this — revocation is via the Knowledge page's Compiled KB review, or
`compiled_kb.revoke()`) stays revoked across future mounts via a sticky
`compiled/kb/unapproved.json` record; a later explicit re-approval clears
it. `./arailctl pkb bootstrap` is also the door for a lab whose World was
mounted before this sprint shipped — those bundles are already sealed and
will never re-enter a mount path on their own, so they need one manual run.

`reembed` — explicit, resumable re-embed of one World's (or the root
lab's) `pkb_pages` vector index with the spec-declared embedding provider
(C2, `sprints/2026-08-08-arail2-tier1-integration/`). This is the **only**
path that (re)writes vectors — nothing else triggers a network embed call;
in particular, an empty or stale index degrades honestly (a status message
naming this command) instead of embedding on demand from inside a search
request.

| Flag | Effect |
|---|---|
| `--world <slug>` \| `--root` \| `--all` | Target one World, the root lab, or every mounted World + root (exactly one required) |
| `--resume` | Resume from the last checkpoint; refuses if the checkpoint's model/dimension/spec disagree with the current spec (never mixes vector spaces) |
| `--dry-run` | Print row count and an ETA from a 32-row timing probe; writes nothing |
| `--yes` | Reserved; this verb never prompts today |

Mechanics: vectors are written into a shadow build
(`<pkb_root>/.cache/lancedb.next/`) batch by batch, with a checkpoint
(`<pkb_root>/.cache/reembed-state.json`) written after every batch. Before
swapping, the shadow build's actual row count is re-read from LanceDB and
compared against the expected total — a mismatch (e.g. an external
cleanup removed `.next`, or a `--resume` checkpoint's claim disagrees
with what's actually in the shadow table) discards the shadow build and
checkpoint and refuses to swap, rather than putting a truncated index
live. A `total == 0` scan is also refused if a live table already
exists — an empty result never replaces a populated index. Only once the
shadow build is verified complete does the live table get replaced — the
previous table is renamed to `pkb_pages.lance.bak-<ts>` first, so a crash
between steps leaves either the old table or the old table plus a
discardable `.next` directory, never a half-embedded live index. An
`O_EXCL` lock file (`<pkb_root>/.cache/reembed.lock`) prevents two
concurrent runs against the same root; a second run refuses immediately
with an actionable message rather than racing LanceDB's own transaction
conflict resolver. SIGINT stops queuing new batches (the in-flight batch
finishes and checkpoints normally) and exits `130`; resume with
`--resume`. A provenance sidecar (`pkb_pages.provenance.json`) is written
last, after the swap.

On a warm Ollama, expect roughly 75–134 rows/s (measured on the live
`ai`/`video-games` worlds — see RESULTS.md in the sprint above); a
5,000-row lab is a ~60s operation. That visibility is the point of having
an explicit verb instead of an implicit one.

Exit: `0` ok · `1` error (LanceDB unavailable, `--resume` checkpoint spec
or shadow-build mismatch, empty corpus against an existing live table,
another `pkb reembed` already running against this root) · `2` the given
`--pkb-root` doesn't exist · `4` the embedding provider is unavailable
(`EmbeddingError`) · `130` interrupted (resume with `--resume`).

### `wiki <op>`

`build`\|`info`\|`new <title>`\|`serve` — ARAIL docs-as-code. Unchanged.

### `world <op>`

DaC WorldBundle ops — passthrough to `python -m arail.world_mount`.
Unchanged.

### `nucleus <verb>`

Model Forge (local domain distillation) — passthrough to
`python -m arail.nucleus`, which owns its own tier gate and exit-code
contract (`0` ok · `1` internal error · `2` usage · `3` refused by
policy). See `docs/nucleus.md` for the full tour.

| Verb | What it does |
|----|--------------|
| `plan "<intent>" --name <slug>` | Writes `configs/domains/<slug>.yaml` and confirms it resolves |
| `stage <slug> --source KIND:NAME=<path>` | Stages a local corpus; makes zero network calls |
| `build <slug> --profile local` | Preflight, then extract / extract-on-cert / train / fuse / eval |
| `spike <slug>` | Gate B half-day M5 harness |
| `certify <build_id>` | Contamination gate, DNA card, seal, build report, local ledger |
| `verify <shard>@<version>|<dir>` | Re-checks a card's signature, key trust, and content hashes |
| `status <build_id>` | Prints a build's `run.json` |
| `list` | Shows certified shards |

### `benchmark_models` (aliases: `benchmark`, `aerollm`)

Measure local model TPS for autoresearch routing. Defaults to `--all`
when no args are given. Unchanged.

### `autoresearch <op>`

Retention for what the tuning loop leaves behind. The loop creates a
baseline branch plus one branch per variant on every pass and appends to
a bench log, and it prunes nothing — run it nightly for a month and you
have hundreds of refs and an unbounded JSONL. These two ops bound that.

**Both ops are dry-run by default.** With no `--apply` they print the
plan and change nothing.

| Op | What it does |
|----|--------------|
| `prune` | Delete old losing/superseded `autoresearch/*` branches |
| `rotate` | Move the oldest bench records into a sibling archive file |

| Flag | Effect |
|---|---|
| `--apply` | Actually make the change (mutually exclusive with `--dry-run`) |
| `--dry-run` | Explicit form of the default |
| `--json` | Emit the plan (and result) as JSON |
| `--keep-recent N` | `prune`: never touch the N newest branches (default 20) |
| `--older-than-days N` | `prune`: never touch anything younger (default 14) |
| `--verbose` | `prune`: also list what was kept, and why |
| `--max-lines N` | `rotate`: how many newest records stay in place (default 5000) |

`prune` never deletes a **win**, the branch you are currently on, a
branch it cannot classify (`unknown`/`running`), the newest N, anything
younger than the age gate, or any ref outside `autoresearch/`. Every gate
is a *keep* gate, so a new failure mode defaults to keeping. Before each
deletion it appends a receipt to `lab/data/autoresearch-pruned.jsonl`
carrying the branch's full SHA and a ready-to-paste `git branch <name>
<sha>` restore line — only the ref is removed, never the objects.

`rotate` discards nothing: the archive is appended to, and the live file
is replaced atomically so a crash cannot truncate it.

Exit: `0` planned, applied, or nothing to do · `2` usage error (bad flag,
invalid policy, `--apply` with `--dry-run`) · `3` applied but one or more
branches failed to delete — the pile is still there, so this is degraded,
not success.

### `kb <op>`

QuKaiZen Karpathy LLM Wiki ops (`status`\|`compile`\|`lint`\|`validate`\|
`query`\|`search`\|`ingest`\|`index`\|`where`). Auto-discovers `KB_ROOT` —
never hardcoded. `kb help` (and the in-repo `kb` help shown once `KB_ROOT`
is known) share one `kb_usage()` function. Unchanged.

### `install-daemon` / `uninstall-daemon`

Supervise the lab with launchd (starts at login, respawns on crash) /
remove that supervision. Unchanged.

### `blueprint <op>`

Configuration-as-Code: `list`\|`catalog`\|`show`\|`create`\|`apply`\|`destroy`.
Unchanged.

### `help` (also: `-h`, `--help`, no verb)

Usage banner.

Exit: `0` (`help`) · `1` (an unrecognized verb).
