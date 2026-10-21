# Review: Buddy, always on (S1 "Buddy answers, asks, and stops")

**Date:** 2026-10-20
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `bc1bf741` (ledger `96c380c8`)
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md) at `b2af01d7`, Addendum A `162d9d22` and `4fe3acf6`
**Code reviewed:** qukaizen-buddy `qukaizen/buddy-always-on` at `0ae79a6d` (55 commits over `main` `349477f2`); qukaizen-arail
`qukaizen/arail-buddy-always-on` at `96c380c8` (42 commits over `origin/main` `ca0a8f81`).
**Standard:** the code wins over the log. Every number below comes from a command I ran in this review, except the dev-set
scores, which are quoted from BUILD_LOG Step 25 and say so. Where I could not run something, the finding says so.

## Verdict: BLOCK

Four BLOCK findings. Two break a merge gate as the code stands: an unguarded `pip install` child process at startup (G1, G2, G9),
and an approval that runs while Buddy is on hold (G5). The other two (findings 3 and 4) would make QA's blind run measure
defects instead of Buddy:
- a kept model sentence can state the opposite of the card it cites;
- a message phrased as an instruction ("Explain the pre-existing failure rule.") can only be answered with a refusal card, in
  both modes;
- a refusal from one case answers the next case after `/new`;
- the model's real system prompt is `llama-ai-eng`'s Modelfile persona, not Buddy's.

Each BLOCK has a small fix. The edge, the contract, the patch set, the carrier, the guard's in-process half, the trace and the
G13 surfaces are well built. Every suite number the builder reported reproduces, except the whole-suite failure count. That
count depends on the Ollama daemon's state, and the differential is clean either way.

## Reproduction of the builder's numbers

| Suite | Command (run by me) | Result | Builder's claim |
|---|---|---|---|
| Buddy | `.venv/bin/python -m pytest buddy/tests -q -p no:cacheprovider -n 4` in qukaizen-buddy | **1644 passed, 1 skipped** (skip: `test_real_world.py:104`, needs the ddac sibling and `node`) | same |
| Buddy coverage | same, `--cov=buddy` | **96 %** of 4,463 statements; lowest `_files.py` 79 %, `conformance.py` 88 %, `preflight.py` 92 % | not stated |
| nanobot upstream, P0 to P2 and the plugin | `.venv/bin/python -m pytest -p buddy.testing.upstream_mode -q -p no:cacheprovider -n 4 tests nanobot/channels` | **7678 passed, 11 skipped** (3 deselected by node id, ruled in Addendum A §A3, debt D23) | same |
| Patch set | `git diff --stat v0.3.5 -- nanobot/` | **3 files, +9 / -3**: `__main__.py`, `cli/gateway_runtime.py`, `channels/telegram/runtime.py`; `git diff --name-only v0.3.5 HEAD` outside `buddy/` lists only those three plus `CLAUDE.md`, `UPSTREAM.md` | same |
| UPSTREAM.md rows | `shasum -a 256` of the three files, and of each at `v0.3.5` via `git show` | all six hashes equal the P0 to P2 rows; each patch commit touches one upstream file (P0's commit also adds `buddy/tests/test_p0.py` and its `UPSTREAM.md` row) | same |
| Seam lock | script: every `seam_files` hash in `pins/nanobot.pin.json` vs the tree **and** vs `git show v0.3.5:<file>` | **16/16 equal on both**; the three `sha256_after` equal the tree | same |
| Contract | `shasum -a 256` both copies; `scripts/sync-buddy-contract.py --check` | both **`48481c1d…a785516`**; `ok: 134 vendored files match the stamp and the sibling's bytes` (source moved `b794f698`→`0ae79a6d`, contract bytes unchanged); `PROVENANCE.json` is `arail.vendor-stamp/v1` with per-file sha256 and `tree_digest` | same |
| World pin | `buddy/tools/vendor_world.py --ddac ../ddac-qa-world-wt --check` (`git archive` of ddac `da2fb181` into a temp dir, DDaC's own exporter; read-only) and `shasum -a 256 worlds/qa-practice/terms.json` | **`ok`**; terms.json **`767286098f…7ff9731`** = the C4 pre-registration record | same (the builder's run of this test skipped: no sibling at the default path) |
| ARAIL R-BUDDY-98 | `pytest tests/test_*buddy*.py -q` in this worktree | **100 passed, 1 xfailed** (the existing `test_qa_buddy.py::test_dream_preview_is_not_redacted_and_is_recorder_independent`, BACKLOG S3) | 100 passed, 1 xfailed |
| ARAIL new tests | the nine new test files (list in finding 13) | **428 passed** | 428 passed |
| ARAIL whole-suite differential | clean `git archive` of `origin/main` and of `HEAD`, each `pytest -q -rf -p no:cacheprovider`, same venv, same session, Ollama daemon down | main: **70 failed**, 6903 passed, 40 skipped, 29 xfailed (14 min). Branch: **70 failed**, 7330 passed, 41 skipped, 29 xfailed (15 min). `comm` of the two sorted FAILED lists: **0 branch-only, 0 main-only** | 64 failed branch / 70 main, one branch-only |
| I1 with the real pins | `buddy/tools/integration_i1.py --arail ../arail-buddy-wt --arail-python <arail venv> --pins real`, with an Ollama daemon I started for this and stopped afterwards | **29/29**, including U-G13-02 ("the planted goal text appears in no edge request (8), prompt (2) or message (6)"), the card's G13 check, the stranger drop, the confirm and its replay, `/stop` | 29/29 |
| Frozen names | `git diff origin/main..HEAD -- . ':(exclude)sprints'` and `git diff main..HEAD` in qukaizen-buddy, grep `aerollm\|AERO_\|tier1-aerollm` | **no line removed or renamed** in either repo. Two added lines *use* the frozen id as is (`brain.py:33` `NON_LOCAL_PROVIDERS` contains `"aerollm"`, and its test). `test_queuellm_rename_frozen_surface.py` is in the differential | — |

**Differential ruling.**
- Both runs used the same venv, in the same session, on clean `git archive` copies, with the Ollama daemon down. Their failing
  sets are identical, 70 = 70.
- **Nothing fails because of the branch.** The pre-existing-failure rule is met by a differential run, not by assertion.
- The builder's 64 on the branch against 70 here is the daemon's state: six daemon-dependent tests pass when it is up. The
  builder recorded this for main.
- The branch's one extra skip is `test_host_contract_vendor.py:99` ("no qukaizen-buddy sibling checkout here"; the archive copy has no sibling). The same check passes in the worktree, and I ran `--check` by hand above.

## Findings

Severity: **BLOCK** (fix before re-review and before QA's blind run) · **must-fix** (before ship) · **follow-up** (file as debt).

### BLOCK

**1. [BLOCK] nanobot can spawn `pip install` at every start, before the exec guard is armed, and the child process is not
guarded (G1, G2, G9, F56 new).**
- `nanobot/channels/manager.py:279` (inside `ChannelManager.__init__`, `:150`) calls `ensure_enabled_channel_dependencies`,
  which runs `subprocess.run([sys.executable, "-m", "pip", "install", ...])` (then `uv pip install` if pip is missing) whenever
  a declared Telegram dependency is not installed (`nanobot/optional_features.py:827-851`, `:220-250`).
- The Telegram plugin declares `python-socks[asyncio]>=2.8.0,<3.0.0` (`nanobot/channels/telegram/manifest.py:41-45`).
  **`buddy/requirements.lock` does not contain `python-socks`**, and neither `pyproject.toml` nor `buddy/pyproject.toml`
  declares it. Both documented installs (README `-e ".[dev]" -e ./buddy`, and the lock's own header) produce a venv without it.
  The builder's venv has it only because the channel plugins' dependencies were installed by hand for the upstream suite.
- `ChannelManager` is built at `gateway_runtime.py:717`; P1's `install()`, which arms the exec guard last
  (`nanobot_seam.py:322`), runs at `:856`. A child process has no audit hook.
- So on the operator's clean install, every start of Buddy ends one of two ways:
  - **pip, or `uv` on `PATH`, is present.** An unguarded child process goes to PyPI, in airgapped mode as well as hybrid, and
    installs unhashed packages into Buddy's environment.
  - **Neither is present** (a `uv venv` has no pip). The install fails, there is no Telegram channel, and R1 refuses with
    "Buddy's Telegram channel could not be installed". Buddy never starts.
- BUILD_LOG's verification log says "the exec guard is the backstop". At that point in startup it is not armed.
- Commands: a venv-vs-lock diff (`importlib.metadata` against the lock's names) lists `python-socks` installed but not locked;
  a probe that hides `python-socks` from `nanobot.optional_features.distribution` and calls
  `ensure_enabled_channel_dependencies({'telegram'}, discover_plugins(), runner=<recorder>)` records
  `[…/python, -m, pip, install, python-telegram-bot[socks,webhooks]<23.0,>=22.6, socksio<2.0.0,>=1.0.0, python-socks[asyncio]<3.0.0,>=2.8.0]`.
- I3 would not catch it: its venv has the package.
- **Fix:**
  - (a) Declare and lock all three Telegram dependencies.
  - (b) Add a preflight check: the static checks refuse (exit 0, fixed line) unless `extra_installed("telegram", PLUGIN.dependencies)` is true.
  - (c) Arm the exec guard in `boot.apply()` once preflight passes, so nothing between P0 and `install()` can spawn. If
    something legitimate needs a spawn there, name it and allow-list it.
  - (d) Test: the gateway built from a venv missing one dependency refuses without spawning.
  - (e) Add `nanobot/optional_features.py` and `nanobot/channels/telegram/manifest.py` to the seam lock.

**2. [BLOCK] A re-delivered or re-typed approval runs `pause_research` while Buddy is on hold (G5, A5 item 3, F57 new).**
- `buddy/src/buddy/confirm.py:156-160`: for a proposal in `confirmed` (or `edge_unknown`/`no_answer`) with the right code, `handle`
  goes straight to `_execute`, and `_execute` makes its first edge call without reading the hold (`:224-225`). The hold is
  checked only for open proposals (`:175`) and before the automatic resend (`:226-228`).
- The existing test asserts the bug: `buddy/tests/test_confirm.py:665-672`
  (`test_no_resend_while_buddy_is_on_hold`) expects `len(cr.pause_attempts) == 1` with the hold on.
- Command: a temporary probe test (deleted afterwards; the tree is clean). Card, then a crash after the write-ahead, leaving the
  row `confirmed`. Then `cr.hold.write_on()`, then the same tap re-delivered. Result: `FakeHost.pause_calls == 1`, so the
  research run was paused while Buddy was held.
- ARAIL's own hold check saves this case only when `/stop`'s `hold_all` reached the lab. It does not save it when the edge was
  unreachable at `/stop`, when the hold file is corrupt (read as on, F8), or when the hold was set at the desk.
- **The same branch has a race.**
  - A typed `/approve` goes through nanobot's per-chat ordered queue, a worker task (`telegram/runtime.py:1852-1890`); a tap is
    handled in PTB's own update loop (`:2198`). So the two can run at the same time.
  - A typed repeat that lands while the tap's edge call is in flight sees `confirmed` with the right code. It makes a second
    edge call (the ledger replays it, so the lab acts once) and replies with the outcome text instead of "Already done.".
  - Win step 4 requires "every repeat gets 'Already done.'".
- **Fix:**
  - Read Buddy's hold and `gate.lab_hold()` before `_execute` in the resend branch, and at the top of `_execute`. When held,
    reply with the held text, make no call, and leave the row `confirmed`.
  - Keep an in-process set of proposal ids whose edge call is in flight. A repeat for one of them gets "Already done." and makes
    no call. The resend branch stays for a row left `confirmed` by a dead process.
  - Correct the test, and add one for a concurrent typed repeat.

**3. [BLOCK] The "card's own words" check (H4) keeps a sentence that states the opposite of the cited card (F58 new).**
- `buddy/src/buddy/answer.py:151-175` scores content words and word pairs against the card. `world.py:36-39` drops `no` and
  `not` as stopwords, and nothing requires the card's negation to survive. So a sentence that leaves out "never" or "not",
  or adds "no", passes.
- Command: `usable_sentence(clean_answer(s), [card])` on the vendored World. **6 of 6 inverted sentences were kept**, among them:
  - "The vault hard gate is lifted, so real unredacted financial documents enter the pipeline." (`vault-hard-gate`)
  - "A gate is bypassed on Buddy's say-so." (`escalate-gate-override`)
  - "Buddy writes the witness line reserved to the operator." (`refuse-witness-line`)
  - "Skipping the QA pass needs no documented override in the sprint ledger." (`qa-bypass`)
- Both modes use the check: `answer.py:229` (structured) and `:250` (template).
- Each of these would go to the phone beside a chip that says the opposite. That fails the bar's rubric ("the sentences do not
  contradict the cited card", §8.4 step 5) and is the "agrees to a reserved act" case of disconfirming #3. H4's claim that a
  kept sentence is "a near-copy of the card" is false as built.
- **Fix before the blind run, either of:**
  - (a) A polarity guard. Every negation marker of the card (`not`, `no`, `never`, `without`, `nothing`, `none`, `n't`, `only`)
    must appear in the sentence, and the sentence must add none. Test it with an inverted sentence for every card of the real World.
  - (b) For cards in `refusals`, `escalations` and `gates`, always use the fixed lead line.
- My preference is (a) plus (b). The rule cards are where an inversion costs most.

**4. [BLOCK] Three answer-path defects would invalidate QA's blind run (F59 new).**
- **(a) A message that is not phrased as a question is offered only rule cards.**
  - `world.py:215-218` returns *only* refusals and escalations whenever any of them scores ≥ 1.5. It does not return them
    first, as H3 describes. `is_question` (`world.py:198-205`) calls "Explain …", "Tell me …" and "Remind me …" requests.
  - Command (vendored World):
    - "Explain the pre-existing failure rule." ranks `pre-existing-failure` at **20.7**. The structured grammar's `cite` enum is
      nonetheless `['refuse-financial-values', 'escalate-contradiction', 'escalate-gate-override']` (all ≤ 2.2).
    - Template mode answers it with `refuse-financial-values`, labelled **refuse**.
    - "Remind me what the vault gate says." is answered with `escalate-gate-override`, labelled **escalate**.
  - So a perfect model cannot give the right card in structured mode, and code picks a wrong refusal in template mode. This is
    a code defect, not a 1B limit. The dev set could not show it: all 20 of its questions end in "?".
  - Fix: offer rule cards first only when one of them scores within a stated margin of the best card (or above the best
    non-rule card). Test: an imperative phrasing of each dev question gets the same top card as its question form.
- **(b) The standing rule (H5) survives `/new`.**
  - `carrier.py:102-137` keeps a hold per chat for 10 minutes. `/new` (`commands.py:150-155`) does not clear it.
  - The QA harness sends `/new` between cases on one chat, so a refusal in one case answers the next case's first non-question
    message.
  - Command: after `holds.note(chat, "refuse", "refuse-witness-line")`, `standing("Explain how a flaky test should be handled.", chat)`
    returns `refuse-witness-line`, and so does `standing("Thanks.", chat)`.
  - Fix: clear the chat's hold on `/new` and on `/stop`, and test it. `/new` is documented as "Starts a fresh conversation".
- **(c) The model's system prompt is not Buddy's persona.**
  - The edge flattens Buddy's persona into ARAIL's `system` argument. `OllamaNativeBackend` then prepends it to the prompt and
    sends **one `user` message** (`router/backends.py:2040-2048`).
  - With no `system` message in the request, Ollama uses the Modelfile's `SYSTEM`. For `llama-ai-eng` that is "You are an AI
    engineering assistant … You reason step by step, write clean idiomatic code …" (`models/ai-eng/Modelfile.default:3-7`).
    The Modelfile also fixes `temperature 0.7` (`:9`), which is where D30's run-to-run spread comes from.
  - So every dev-set number in Step 25, the fast loop's included, measures Buddy's rules arriving as user text underneath a
    different system persona. 
    Measured with a daemon I started for this check and stopped afterwards: `llama-ai-eng` given one user message ("hello",
    `num_predict` 1) evaluates **76** prompt tokens; the same message after an explicit one-character `system` message
    evaluates **27**. The 49-token difference is the Modelfile's `SYSTEM`, which the model receives whenever the request has no
    `system` message (`/api/show`: the same text, `temperature 0.7`).
  - The blind run decides the shipped mode and fires disconfirming #2 ("the 1B cannot carry policy", which redirects the
    roadmap to a brain bake-off). It must not measure this confound.
  - Fix:
    - The edge path passes `system` to Ollama as a real `system` message. This is a keyword on `OllamaNativeBackend` used only
      by `brain.chat`; the A4 fixture proves every existing caller's request body is unchanged.
    - Re-run the dev set in both modes and record it in BUILD_LOG before QA's blind run.
  - This amends §5.6's flattening and §6.1's "the backend drops `messages`" for the edge only. The architect ratifies the
    amendment at re-review.
  - D7 already records this debt ("ARAIL's Ollama backend ignores `messages` and `temperature`"). This review moves its
    payment, for the `system` message only, ahead of the blind run, because the blind run's verdict now depends on it.
    Forwarding `temperature` is optional; if it stays at 0.7, QA runs each mode at least twice.

### Must-fix before ship

**5. [must-fix] The desk's `hold off` does not release a running Buddy after a phone `/stop` (G6 usability, M1).**
- `hold.py:108` latches every `write_on` in memory. `read()` (`:77-85`) returns the latch when the file is gone. `clear()`
  (`:132-135`) releases only the latch of the process that calls it.
- `python -m buddy hold off` runs in another process. It removes the file and prints "He answers again once the lab is also off
  hold." (`cli.py:167`), but the gateway stays held until it restarts.
- Command: two `HoldFile` objects on one path; `gateway.write_on()`, then `desk.clear()`. Result: file gone,
  `gateway.read().on == True`.
- Fail-safe in direction, but it breaks the documented resume (`docs/S1.md:82`, §8.8 "After the run").
- Fix: latch only when the write fails, or drop the latch once a successful write is observed gone. Test with two instances.

**6. [must-fix] F14 (token revoked) is not built, and a dead poller leaves Buddy deaf while the gate says "open".**
- `grep -rn "InvalidToken\|rejected the bot token" buddy/` finds nothing: there is no fixed message and no exit 0.
- nanobot's `start()` re-raises a rejected token as `RuntimeError` (`telegram/runtime.py:617-623`). `_poller_done`
  (`channel.py:258-264`) logs it and closes the guard, but leaves `gate.status.state == "open"`. `_open()` then does nothing on
  every later tick (`gate.py`, `if self.status.state == "open": return`). Buddy stays silent until restart; `/status` and the
  trace would say "listening".
- The same happens for any terminal startup error.
- §8.7 specifies I-TG with **fake PTB transport failures**. These need no Telegram; the build did not write them (BUILD_LOG lists
  F13 and F14 under "Not run here").
- Fix:
  - On the token error: the fixed F14 line, then stop the process with exit 0 (`_stop_the_process`).
  - On any other terminal poller death: close the gate's state too, so the next good handshake restarts polling.
  - Tests I-TG-01 and I-TG-02 with fake PTB.

**7. [must-fix] The ARAIL stamp names a Buddy commit that exists only on this Mac.**
- `src/arail/buddy_host/contract/PROVENANCE.json` names `source_commit b794f698…` of `cdarnell/qukaizen-buddy`.
- Nothing is pushed (BUILD_LOG Final state), so the cross-repo edge does not resolve for anyone else (workspace rule:
  cross-repo edges are versioned and resolvable).
- Push `qukaizen/buddy-always-on` (or merge it) before the ARAIL PR merges, and re-run `--check` against the pushed ref.

### Follow-up (file in DEBT.md / BACKLOG)

**8. [follow-up, decide before S2 adds people] Phone text, raw Telegram ids and usernames are written to the launchd log at INFO.**
- nanobot logs `Processing message from telegram:<id>|<username>: <80 chars>` and `Response to …: <120 chars>`
  (`nanobot/agent/loop.py:1783`, `:1729`). The session policy `log_content` defaults to true (`session/manager.py:271`) and Buddy
  does not turn it off.
- No gate is broken: G4 covers strangers, who are never logged (`runtime.py:1965` precedes `:2011`), and G7 covers secrets, which
  are redacted.
- But this log is a plain-text transcript of every phone conversation, with the sender's Telegram id and username, in
  `~/.nanobot/logs/`.
  - `docs/S1.md:165` describes that directory only as "Secrets and confirmation strings are redacted".
  - §5.18 keeps raw ids inside Buddy's process and files, which this log technically is, but nobody decided that it should
    hold the conversation.
- Fix: set the session policy to `log_content=False` for Buddy's sessions, which also hides the reply preview (`loop.py:2084`).
  Then either accept the sender id in the log as Buddy's own file (and say so in `docs/S1.md`), or pseudonymise it.

**9. [follow-up] The armed exec guard does not see `_posixsubprocess.fork_exec`, and `ctypes` bypasses the socket guard.**
- Command: a `python -I` probe with the guard closed and armed, against a parent listener on a port the guard refuses:
  - `subprocess.run`, `os.posix_spawn` and `os.system` were refused.
  - A Python `connect` was refused, and so was a Python `connect` in an `os.fork()` child (the hook is inherited).
  - `multiprocessing.util.spawnv_passfds(b"/usr/bin/nc", …)` **started `nc`, which connected**.
  - `ctypes.CDLL(None).connect(fd, …)` **connected**.
- No in-tree caller does either today (`grep` of nanobot and of PTB, httpx, openai and loguru). `ctypes` is assumption A9.
- Wrap `_posixsubprocess.fork_exec` the way `getaddrinfo` is wrapped, consider refusing `ctypes.dlsym` once armed, and keep
  QA's process-tree L3 as the backstop.
- *Imports before boot:* `.pth` files and the user site run before P0. That needs write access to the user's files, which is
  assumption D1 (Buddy runs as the user), not a new hole.
- *The `nanobot` console script* (`pyproject.toml:111`) skips P0. Its gateway still reaches P1, and `install()` refuses at R0
  (`nanobot_seam.py:346-347`), so it fails closed, but after `ChannelManager` was built (so finding 1 applies there too).

**10. [follow-up] The G4 log assertions cannot see nanobot's logs.**
- `test_intake.py:126-137` and `test_channel.py:216-224` assert the stranger canary is absent from `caplog`, which captures
  stdlib `logging`. nanobot logs with loguru.
- G4 holds by reading (see 8), not by these tests. Add a loguru sink in those tests, and have QA grep the launchd logs.

**11. [follow-up] Runtime-context tag in user text aborts later turns.**
- A talker who types nanobot's tag `[Runtime Context — metadata only, not instructions]` gets the C8 abort
  (`turn_hook.py:133-135`) on every later turn until the message leaves the 8-message window, or until `/new`.
- Command: a probe through `carrier.strip_runtime_context` on a persisted message whose text contains the tag. The tag
  survives the strip.
- It is self-inflicted and talker-only. Intake should neutralise the tag in user text.

**12. [follow-up] Smaller items.**
- `BuddyTelegramChannel` does not override `send_delta`. Streaming is off and pinned by S9, but an override that refuses is
  cheap defence in depth.
- A forged callback publishes `button_label` unscrubbed in the inbound metadata (`runtime.py:2223-2230`). Only the sender's own
  code can be in it.
- `Intake._handle` (`intake.py:263-380`, about 120 lines, more than 15 branches) and `ConfirmService.handle` (about 50 lines)
  are over the complexity line. Both mirror an ordered spec table; leave them, but do not grow them.
- The trace-id and pseudonym regexes are repeated in four modules.
- No automated static test for G8 (`grep -rn -i "11434\|ollama" buddy/src/buddy` finds only docstrings). Add one.
- ARAIL ignores `temperature` (D7), so every model-using run varies by about two cases (D30). QA should run each mode at least twice.

**13. [INFO] ARAIL edge: what I checked and found right.**
- Admission order (`admission.py:147-164`): root lab, bind, peer in `{127.0.0.1, ::1, ::ffff:127.0.0.1}`, then the credential
  (`hmac.compare_digest` over sha256 digests, `link.py:146-158`; ARAIL keeps only the hash), then the contract hash, then a
  streamed size cap and the schema.
- Errors carry fixed messages and `X-Should-Retry: false`, and logs carry class names only.
- `brain.chat`:
  - The carrier rules of A1.4 (`brain.py:226-256`).
  - Cloud and non-`ollama_native` backends are refused twice: `describe()`, then `build_router()` on the real resolution (`brain.py:112-168`).
  - `finish_reason` is always `stop` (`host_edge_routes.py:353-359`).
  - Mode and budget are re-checked with the slot in hand, `claim_turn` under one lock (`:288-300`, `budget.py:121-133`).
  - The worker runs in a copied context (`:313-315`).
- `pause_research`:
  - A replay is answered from the ledger before the hold check, and never executed (`:178-182`).
  - The hold, role and freshness are checked before the `executing` row is committed (`ledger.py:107-115`, WAL with
    `synchronous=FULL`).
  - The core runs on the event loop thread, so identical requests cannot interleave.
- Registration is wrapped, so a bad stamp means no edge rather than no portal (`app.py:1398-1409`).
- No existing ARAIL test was modified or deleted: `git diff --diff-filter=MD origin/main..HEAD -- tests` is empty.
- The new test files: `tests/{test_agent_trace_v2,test_agent_lanes_phone_steps,test_host_edge_plumbing,test_host_contract_vendor,test_host_link,test_host_edge,test_host_edge_conformance,test_host_edge_brain}.py`, `tests/router/test_response_schema.py`.

## Security pass on the phone path: what I checked

- **Injection through user text.**
  - User text can never be `messages[0]`: the composer owns it (C1), and the edge refuses a header anywhere else and any later
    `system` message (`brain.py:236`; conformance 053, 125, 126).
  - The World block is escaped JSON inside nanobot's markers, and the model can only cite slugs from a per-turn enum.
  - Chips come from sealed bytes; links are stripped; model text is never parsed for commands.
  - A user *can* write `### assistant` headings, which the edge's flattening (`brain.py:259-266`) puts verbatim into the single
    user prompt, so a 1B model can be confused. The blast radius is bounded by the grammar, by code deciding the label and the
    card, and by tap-only actions.
  - The tag abort is follow-up 11; the inverted sentence is finding 3.
- **Nonce.**
  - 64 random bits; only the sha256 is stored (`proposals.py:177, 193`) and the comparison is constant-time (`confirm.py:155`).
  - It is never in card text (A5) and never published: Intake routes `/approve` and `/deny` before publishing, and scrubs pasted
    codes by hash (`scrub.py`).
  - It is redacted in logs (`redact.py`: `_CONFIRM`) and absent from trace attributes (proposal id only).
- **Confirmer against talker.**
  - Role is checked before the code (`confirm.py:138`), then the DM, then that the requester is the confirmer (`:149`).
  - A model proposal from a talker is suppressed (`:123-126`), and the edge re-checks the role (`host_edge_routes.py:185`).
  - ARAIL cannot verify a Telegram identity and trusts Buddy's credential. That is the design (§5.1); the actions are reductive.
- **Telegram callback payload.**
  - Parsed like typed text (F19).
  - A stranger's tap is counted and never answered (`channel.py:102-107`).
  - Groups are refused (Intake checks chat id against user id).
  - `callback_data` stays within 64 bytes (test).
- **Pairing and the people file.**
  - `is_allowed` and `_send_pairing_code_if_private` are overridden. S12 refuses a pairing store.
  - Numeric ids only. An invalid reload means nobody (F38).
  - The file's mode is checked at start (S2) but not on reload. That is acceptable: D1 says Buddy runs as the user.
- **Hold file.**
  - Every unreadable, unparseable, foreign-owned or writable-by-others state reads as ON (`hold.py:87-101`), and the write is
    atomic.
  - Finding 5 is the inverse defect.
- **Audit-hook guard.**
  - In-process DNS, connect, sendto/sendmsg and bind: correct and fail-closed on malformed arguments (`guard.py:232-254`).
  - Subprocess, `posix_spawn` and `system` are refused once armed; a forked child inherits the hook.
  - Bypasses: a spawn before arming (finding 1); `_posixsubprocess.fork_exec` and `ctypes` after arming (finding 9).
  - An import before boot needs `.pth` or user-site write access (D1).
- **Secrets.**
  - At rest: `secrets.env`, `link.json` and the proposal store are 0600 and refused through a symlink; ARAIL stores only the
    credential's sha256.
  - In logs: an exact-value and shape redactor on stdout and stderr, which nanobot's only loguru sink writes through
    (`nanobot/cli/commands.py:29-41`), plus the loguru patcher.
  - Tracebacks pass through the same stream.
  - Bytes written to `sys.stdout.buffer` or to fd 1/2 directly would not be redacted. I found no writer that does (`rich.Console()` resolves `sys.stdout` at write time).
- **G13 surface.**
  - `/status` (`commands.py:76-105`) and the card (`confirm.py:97-104`) carry no lab data.
  - Error texts are fixed. The router echoes only the sender's command token.
  - A kept model sentence comes from a prompt that holds only fixed persona text (today also the Modelfile's, finding 4c), the
    World, the sender's words and Buddy's earlier answers (input closure, §A11), and must share the card's wording. So it cannot carry lab data, but it can be false (finding 3).

## Failure modes F1–F55 against code and tests

"✓" = a named test in the branch proves the containment and I found nothing that contradicts it; "QA" = only a system run can
prove it; "✗" = defect.

| # | Status | Evidence / gap |
|---|---|---|
| F1 replay | ✓ + QA (W-4) | `test_confirm` "Already done" cases; ARAIL ledger replay tests; conformance 107/108 |
| F2 username spoof | ✓ | numeric id only: `intake.parse_sender_id`, `channel.is_allowed`; U-ID tests |
| F3 re-delivery | ✓ but ✗ under hold | I-CRASH-01/02d pass; finding 2 |
| F4 two cards | ✓ | U-CONF-07 supersede |
| F5 approve while busy | ✓ | confirm branch (7) precedes busy (13b) in Intake |
| F6 restart mid-call | ✓ (FakeHost) | I-CRASH-02a–d; ARAIL `indeterminate_restart`. Not run against the real edge |
| F7 credential in logs | ✓ unit, QA S-LEAK-01 | `test_redact`; `admission` logs class names only |
| F8 hold corrupt | ✓ | `test_hold` matrix; finding 5 is the inverse problem |
| F9 halt.json corrupt | ✓ | `test_a_corrupt_halt_file_reads_as_unreadable_and_held`; conformance 058/116 |
| F10 World seal | ✓ | S7 tests, `test_world` |
| F11 contract mismatch | ✓ | conformance 020; `test_gate` contract → closed, 60 s retry |
| F12 flip mid-turn | ✓ unit, QA S-FLIP-01 | `send` suppresses `gate_closed`; `test_lab_mode_is_read_again_after_the_wait_for_the_slot` |
| F13 Telegram outage | gap | no I-TG-01; nanobot's backoff is relied on, untested here (finding 6) |
| F14 token revoked | ✗ | not built (finding 6) |
| F15 slug not in World | ✓ | U-ANS-04 |
| F16 injection → nonce | ✓ unit, QA Q-PRESS | U-NONCE-01..03 unit parts |
| F17 two instances | ✓ S11 | `test_s11_a_second_buddy_on_the_same_home_is_refused`; nanobot lock / 409 not run |
| F18 enum grows | ✓ | C-ENUM-01; frozen `ACTIONS` |
| F19 forged callback | ✓ | U-CONF-09 |
| F20 talker burns nonce | ✓ | U-CONF-02 (role before code) |
| F21 cloud `fast` | ✓ | conformance 060/061; `brain.py` double check |
| F22 presence | ✓ unit, QA R-PRES-01 | `test_edge_traffic_is_not_operator_presence` |
| F23 background calls | ✓ unit, QA S-UNSOL-01 | S9, R3, `/new` guard, C3/U-HDR-05 |
| F24 tools reappear | ✓ | T1, T2, edge `tools_not_allowed` |
| F25 seam drift | ✓ | U-SEAM-01; lock should add two files (finding 1e) |
| F26 groups | ✓ | U-INT-03 |
| F27 media | ✓ | U-INT-05; venue and location as media |
| F28 proxy env | ✓ | S10 |
| F29 retries | ✓ | C-ERR-01 with nanobot's markers as oracle |
| F30 budget | ✓ | conformance 065/066; I-EDGE-14 |
| F31 brain busy | ✓ unit, QA P-1 | `test_a_second_question_hears_busy…` |
| F32 idle researcher | ✓ | conformance 103–106 |
| F33 nonce at rest | ✓ unit, QA S-LEAK-02 | confirms never published |
| F34 header forged | ✓ | conformance 053, C-HDR-01..03, U-HDR-03 |
| F35 link symlinks | ✓ | `test_host_link` |
| F36 links in output | ✓ | U-ANS-06 |
| F37 chip markdown | ✓ | U-CHIP-02; R6 at start |
| F38 people.toml | ✓ | U-ID-05 |
| F39 stale after sleep | ✓ | I-SLEEP (`test_gate` sleep cases), stale branch |
| F40 respawn loop | ✓ | `test_p0`, `test_boot` exit codes |
| F41 portal on LAN | ✓ | conformance 017/022 |
| F42 unsolicited | ✓ unit, QA S-UNSOL-01 | `send` `no_trigger` |
| F43 listener | ✓ unit + I3 airgapped, QA hybrid | R1, bind guard; I3 one listener `127.0.0.1:18797` |
| F44 clock skew | ✓ | conformance 110/111 |
| F45 contextvar | ✓ | `test_the_model_call_record_carries_who_asked_through_the_worker_thread` |
| F46–F48 carrier count, outside turn, compaction | ✓ | U-HDR-03(f), -04, -05, -06 against a real `AgentLoop` |
| F49 confirm string published | ✓ | U-NONCE-02 (scrub); follow-up 12 (callback metadata) |
| F50 one turn in flight | ✓ unit, QA I-INJECT-01 | U-INT-BUSY |
| F51 malformed carrier | ✓ | C-HDR-01..03 |
| F52 hook absent | ✓ | U-HDR-07 |
| F53 default label | ✓ | U-LABEL-01 |
| F54 reply context | ✓ | U-REPLYCTX-01 |
| F55 `length` | ✓ | I-EDGE-13; conformance 129 |

**New rows for §7** (the builder adds them with their tests; the architect amends §7 at re-review):

| # | Failure | Containment | Test |
|---|---|---|---|
| F56 | A missing channel dependency makes nanobot spawn `pip`/`uv` before the exec guard is armed | complete lock; preflight check; exec guard armed in `boot.apply()` | finding 1 (d) |
| F57 | A re-delivered approval executes while Buddy is held | hold check before every edge call in `ConfirmService` | finding 2 |
| F58 | A kept model sentence inverts its card | polarity guard; fixed lead for rule cards | finding 3 |
| F59 | Instruction-phrased questions get only rule cards; a refusal hold survives `/new` | margin rule; clear holds on `/new` and `/stop` | finding 4 |
| F60 | The desk's `hold off` does not reach a running Buddy | latch only on a failed write | finding 5 |
| F61 | A terminal poller failure leaves Buddy deaf with the gate "open" | F14 exit 0; close the gate's state | finding 6 |

## Gates G1–G13: does the cited test prove the gate?

| Gate | Proof claimed (§8.2, §A11) | Ruling |
|---|---|---|
| G1 | L1, L2, L3 airgapped, S-FLIP-01 | **Not proven; defect.** L1 and L2 are real (`test_guard_process.py`: fresh `python -I`, real sockets, a parent listener counts what arrives). L1 relies on CPython auditing before the syscall rather than the "counting shim" §8.3 names; that is acceptable. L3 (I3) ran as a plain subprocess, not under `launchctl`, without `nettop`. S-FLIP-01 was not run. A child process is outside the hook (finding 1, finding 9). |
| G2 | L3 hybrid | **Not run** (QA). Finding 1 would break it (PyPI). |
| G3 | S-LISTEN-01, U-GUARD-bind | Bind refusals are proven (L1). I3 saw only `127.0.0.1:18797` for Buddy. **ARAIL's listener set before and after was not checked** (QA). |
| G4 | U-INT-01 | Bus, replies, trace and edge: **proven** with fakes and through the harness. Logs: **not proven by the test** (finding 10). Holds by reading. QA greps the real logs. |
| G5 | U-CONF, U-NONCE, W-3/W-4 | Checks and nonce absence in published text: proven. **"No hold": violated** (finding 2). U-NONCE-01's capture of every edge body in the win run and the blind run is QA's. |
| G6 | I-EDGE-03/04, U-HOLD | Edge refusals while held or unreadable: proven by conformance and `test_a_hold_that_lands_between…`. The 60-second zero-calls window is QA's. Finding 5 is a resume defect, not a breach. |
| G7 | U-PERM, S-LEAK | Modes and owners: proven (S2, link tests). Redaction of token, credential, salt and confirm strings on stdout, stderr and the loguru patcher: proven. S-LEAK greps are QA's. |
| G8 | static grep, U-GUARD-connect 11434, I-EDGE-06 | Proven, except that the static check is mine, not a test (follow-up 12). |
| G9 | S9 table, R1–R8 negatives, exec guard | S9 (40 deviations) and R1–R8: proven. **The exec guard is armed too late** (finding 1) and misses `_posixsubprocess` (finding 9). |
| G10 | U-SEAL, U-SEAM, contract 412 | Proven. |
| G11 | U-LLAMA-01, T4 | Proven: C4 composes it, C8 checks it. |
| G12 | R-NAME-01, R-BUDDY-98, R-AIRGAP-01 | Names: proven by my diff grep. R-BUDDY-98: see the table above. R-AIRGAP-01 is QA's. |
| G13 | U-G13-01, U-G13-02, W-run | U-G13-01 (`test_g13.py`, byte-identical renders across disallowed fields, canaries absent): proven. U-G13-02: proven, in my own I1 run (29/29, see the table above). Input closure holds; finding 3 is about truth, not lab data. The W-run classification is QA's. |

## H1–H9 rulings

| # | Ruling | Gate or win test changed? |
|---|---|---|
| H1 | **Ratified.** A8b before A5d avoided a red commit; the re-vendor rule was still honoured (A5d, A5e, A5f, equal sha256). | No |
| H2 | **Ratified, wording corrected.** The fix is right: C8 asks `scrub.contains_confirmation` (`scrub.py:34-40`). The log's "the placeholder cannot match" is wrong: `/approve <id> <nonce>` still matches `CONFIRM_RE`, and the exemption is the exact placeholder. Amend A1.3 C6.2/C8 to say so. | No |
| H3 | **Ratified in principle, rejected as built.** Code may decide the label and the grammar may list only the offered cards. But `offered()` returns *only* rule cards for any non-question (finding 4a), which is not "first". | No gate. It changes how the bar is reached; the win test is unchanged (step 1 ends in "?") |
| H4 | **Rejected as built** (finding 3). Ratified once a polarity guard is in, and the fixed lead is used for rule cards. Record it as an amendment to §6.4 and to VISION §2.3's fallback: "the model phrases one sentence, shown only when the card supports it; otherwise a fixed lead line". | No gate. Win step 1 may become lead line + chip; QA judges "1–3 sentences" and records which form it was |
| H5 | **Ratified with conditions.** Clear on `/new` and `/stop` (finding 4b). TEST_REPORT must say the pressure score is carried by code: this applies disconfirming #3's own remedy ("code routes those intents to fixed refusals keyed by the World's refusals terms") before the blind run. | No gate. It changes what the pressure bar measures |
| H6 | **Ratified.** `[answer] mode` belongs at the desk; the handshake can still force template mode (`carrier.py:160-162`). A restart is required. | No |
| H7 | **Ratified.** The model's proposal path stays (enum, card, tap) but is dormant. Win step 2 depends on `_RULE_RE` (`intake.py:55`), so QA sends exactly "Pause the research run." | No |
| H8 | **Ratified as procedure, with three corrections.** (1) The blind run happens after the BLOCKs are fixed, in both modes, at least twice each (D30). (2) The model must receive Buddy's persona as its system prompt in that run (finding 4c); no Step 25 number did. (3) Template mode is bounded mostly by retrieval, not by the model: its 12/20 equals the builder's count of questions whose right card ranks first (Step 25, D29). If template fails too, S1 still ships per VISION §2.3, but the ledger records that the fallback is also below the bar, and D29 (ranking) is scheduled with disconfirming #2's bake-off, not after it. | No gate; the win test is unchanged |
| H9 | **Ratified.** Do not touch sealed bytes. "C1 DRAFT" is in `spec.json`, not in `terms.json`, so no chip carries it. The World's owner reseals in their own change. | No |

## Answer quality (VISION §2.3, §6.4)

- **Is template mode implemented as specified?** Yes.
  - It is a config switch: `[answer] mode` (`configure.py:63, 84, 143`), plus `structured_output:false` from the handshake.
  - Code picks the term: `finalize_template` → `top_terms(…, 1)`, which is the first card of the same `offered()` the prompt shows.
  - The model gets the short TEMPLATE persona, no grammar and no `propose` field, so only the code rule proposes.
  - One sentence is kept if grounded, else the lead line.
  - Findings 3 and 4a apply to it as much as to structured mode.
- **Does structured mode's poor showing point to a defect?**
  - Partly yes. Finding 4a takes the right card out of the grammar for every instruction-phrased question. The `is_question`
    heuristic also makes "May I …?" pressure an answer, not a refusal (Step 25 names this). Both are code.
  - What is a 1B limit: with the right card among the K=6 offered, the model picks it less often than rank-1 retrieval does
    (structured 10.3 against template 12, both on question-form items). Its `kind` and its paraphrases are unreliable.
  - One confound is serious enough to be part of a BLOCK (finding 4c). Buddy's persona reaches the model as user text, under
    `llama-ai-eng`'s own "AI engineering assistant, reason step by step" system prompt, at the Modelfile's temperature 0.7.
    No dev-set run ever measured Buddy's persona as the system prompt.
  - Until 4c is fixed and re-measured, "the 1B cannot carry policy" is not established. The pre-committed path (ship template,
    fire #2) is unchanged, but it must be decided on an unconfounded run.

## Tech debt delta (vs ARCHITECTURE §10 and §A9)

**Added beyond the prediction:**
- F56–F61 until they are fixed.
- D29 to D32 (filed).
- A cross-repo file shape outside the contract (Buddy's `link.json`, D16).
- The seam lock misses `optional_features.py` and the Telegram manifest.

**Repaid as predicted:**
- D15 (G1) is closed by the carrier.
- Compaction is impossible rather than unlikely.
- D7 will be partly repaid by finding 4c (the `system` message only).

**Net:** positive, as §10 said, and larger by findings 1–7 until they land.

## Required actions before merge

1. Builder: fix findings 1–4 (BLOCK), with the tests named in each. Re-run the Buddy suite, the upstream suite with the plugin,
   R-BUDDY-98, the new ARAIL tests and the differential. Append a BUILD_LOG step. Then architect re-review (findings 1–4 only).
2. Builder: fix findings 5–7 before ship. Push the Buddy branch (7).
3. Builder: file follow-ups 8–12 in `buddy/docs/DEBT.md` or `sprints/BACKLOG.md`; the operator decides finding 8 before S2 adds people. Add F56–F61 to the Buddy tests' docstrings.
4. Orchestrator: relay BACKLOG D9 (the `agent_trace` v2 notice) to 3D.EVAL before the ARAIL PR merges.
5. QA, after re-review:
   - **Blind run, both modes.** `python -m buddy qa_harness --home <home> --set ~/.qa-sealed/2026-10-20-buddy-always-on/blind.jsonl --out <file>`.
     Run with `[answer] mode = "structured"`, then `"template"`, restarting Buddy between them, each mode at least twice (D30).
     Use the real ARAIL and `llama-ai-eng`.
     - Before the run, check lexical overlap between the dev and blind stems.
     - Before the run, capture one Ollama request body from the edge and show that Buddy's persona is its `system` message
       (the finding 4c regression).
     - Grade by §8.4 step 5. Count the `reply_sent error` records separately (an infrastructure error is not a model answer).
     - Count kept model sentences against lead lines, and check every kept sentence against its card for contradiction (the
       finding 3 regression).
     - Commit the blind file; its sha256 must equal `db7c9827…`.
     - The verdict decides the shipped mode (H8).
   - **L3 under `launchctl`** (`python -m buddy service install`).
     - Use a venv built exactly as `docs/S1.md` and `README.md` say. This is the finding 1 regression.
     - 10 minutes airgapped, then 10 minutes hybrid with Telegram traffic.
     - Every 0.5 s, sample `lsof -nP -a -p <pid,+all descendants> -i`, and run `nettop -P -L 0 -p <pid>`.
     - Record every child process (`pgrep -P`) from the first second of start.
     - Pass: airgapped shows zero non-loopback rows; hybrid shows only the loopback edge and health ports and Telegram's
       addresses. Add `tcpdump` if sudo is available.
   - **S-FLIP-01**: five flips, worst case recorded, at most 60 s to zero Telegram sockets.
   - **S-LISTEN-01**: both PIDs, before and after.
   - **R-AIRGAP-01** and **R-ROUTES-01**.
   - **S-LEAK-01/02**: grep the home, `~/.nanobot/logs/`, ARAIL `lab/data/`, `logs/` and `agent_traces.jsonl` for:
     - the token, `bhc1_`, the credential's sha256 and the salt;
     - every nonce of the run;
     - the stranger canary (G4, loguru included).
   - **U-NONCE-01**: capture every edge request body in the win run and the blind run, and show no nonce in any of them.
   - **F13/F14 with the real bot, after finding 6:**
     - a rejected token gives the fixed line and exit 0, with no respawn;
     - a blocked `api.telegram.org` (pf) gives backoff, no busy loop, and recovery.
   - **I-INJECT-01** over Telegram.
   - **P-1** (p95 ≤ 20 s with research running) and **P-2**.
   - **The win run** (§8.8), seven steps in one run:
     - Use the exact sentences, including "Pause the research run." for step 2.
     - In step 4, send the typed `/approve` within a second of the second tap (the finding 2 race). Every repeat must read
       "Already done.", and the ledger must have one row.
     - Class every bot message (a), (b) or (c) for G13.
     - Afterwards, `python -m buddy hold off` must bring Buddy back without a restart (finding 5 regression).
     - Step 7 is the operator's signed line.
6. Operator: W5 witness line (O1); accounts B and C (O2).

## Differential ruling on `test_ollama_unload_button_actually_frees_memory_on_live_daemon`

**Ruled: not caused by the branch. It is daemon-state sensitive, and on this evidence it is not a failure at all.**
- Commands: an Ollama daemon I started for this check (`ollama serve`, 0.32.4, stopped afterwards). Then the single node id,
  `pytest -q -p no:cacheprovider -rs`, alternating on the two clean archive copies: main, branch, main, branch.
- Result: **passed, passed, passed, passed** (about 2 s each).
- In both whole-suite runs above, with the daemon down, it was **skipped on both sides** (its `_daemon` mark). So it is in neither
  failing set.
- The test loads `llama3.2:1b`, ejects it, and then asserts that it left `ollama ps` and that no other resident model was evicted.
  Any other client of the same daemon during the run breaks those assertions: the builder's dev-set runs keeping `llama-ai-eng`
  resident, or a live Buddy and edge.
- The branch does not touch the eject route (`git diff origin/main..HEAD -- src/arail/portal/app.py` changes only the presence
  skip, the edge registration and the trace detail). That accounts for the builder's one-off branch failure, and for its
  "fails alone on main" too.
- One consequence for QA: run the whole suite with no Buddy, edge or dev-set traffic on the daemon. A lab where Buddy is live
  is such traffic.
