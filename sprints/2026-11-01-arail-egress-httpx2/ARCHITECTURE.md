# Architecture: the egress guard covers the httpx family (TD50, and the runtime half of TD51)

**Date:** 2026-11-01
**Spec:** [SPRINT.md](./SPRINT.md) at `34dc5f2a` (Task, Win condition, Root cause, Baseline B1 to B8). There is no VISION.md: the visionary phase was skipped on purpose, and the win condition is the operator's brief, quoted in SPRINT.md.
**Base:** `origin/main` `5997363e` (#210). Independent of PR #211; the only file both PRs touch is `sprints/BACKLOG.md`, and that edit is this PR's last commit, made after #211 merges.

Every claim marked *verified* below was run by the architect on 2026-11-01 against the real libraries, with a scratch prototype of the guard injected at runtime (the worktree was not edited). The probe scripts are in the session scratch directory (`<scratch>/arch/probe_*.py`, not committed). The Appendix lists each result with what it means.

## Decisions (read first)

| # | Question | Decision | Why (evidence) |
|---|---|---|---|
| 1 | Guard mechanism | A **fixed tuple** `arail.httpx_family.FAMILY = ("httpx2", "httpx")`, plus **test-time tripwires** (no runtime discovery). For every installed member, patch `HTTPTransport.handle_request` and `AsyncHTTPTransport.handle_async_request` at class level, as today. State is a dict keyed by **(class, attribute)**: `(owned, original)`. Reset does `setattr` if the class owned the attribute and `delattr` if it inherited it. Idempotency comes from `_INSTALLED` plus the per-method marker `_arail_guarded` (and `__wrapped__`). An RLock guards install and reset. **Installed but unpatchable** (the import raises, or a class or method is missing): patch everything else, record the reason in `coverage()`, log at error level, then **raise `EgressGuardIncomplete`**. `_INSTALLED` stays False, so every later call retries and raises again. Make the saved `requests` adapter capture idempotent. Match `_SKIP_MODULES` by **exact name or dotted prefix**, with `httpx2` and `httpcore2` listed by name. | Runtime discovery would import arbitrary packages at portal start. A red CI run beats an unseen guess. *Verified:* `httpx2.alias_httpx()` called before install, with the family in either order, wraps once, writes one audit line per refusal, and reset restores the original (V5). *Verified:* a retried install after an incomplete one overwrites the saved `requests` adapter, so `_reset_for_tests()` leaves `GuardedHTTPAdapter` in place. That is a latent bug, and this design makes the path reachable (V6). *Verified:* `httpx2` frames are skipped in the audit `caller` only because `"httpx2".startswith("httpx")`. With exact matching and no explicit entry, the caller becomes `httpx2._client.Client._send_single_request` (V7). Raise, not log-only: every caller of `install_guard` already turns an exception into a visible signal (portal activity warning, loader warning, doctor's required check, nucleus worker exit 1). A log line alone is the silent no-op that caused TD50. Raise in hybrid too, because the toggle flips `LAB_MODE` in-process (`app.py:12152`). *Amended by the review (2026-11-01, REVIEW.md B1): for the portal and the agent loader the "visible signal" turned out to be one activity-log line and a bullet in a collapsed modal section, while both keep serving with that library unguarded and the modal still says airgapped is enforced. The UI must use `guard_installed`/`uncovered()`, the decision must be stated, and fail-closed is filed as TD56.* |
| 2 | Is class-level transport patching the right choke point for httpx2? | **Yes. Keep it.** Document the paths that bypass it and pin them with tripwires. Closing them is new debt (TD53, TD54). | *Verified:* the transport design is identical in httpx 0.28.1, httpx2 2.14.0 and httpx2 2.16.0 (diff of `_transports/default.py`, same exported transports). *Verified refused* (V2 to V4): huggingface_hub 2.4.0 and 1.25.1 (`model_info`, `hf_hub_download`, `snapshot_download`, a shared session built before install), the default clients of anthropic 2.3.0 and openai 3.42.1 (built before and after install), `proxy=` (http and socks5) at loopback, `mounts=`, env `HTTP(S)_PROXY`, `transport=HTTPTransport(...)`, a subclass calling `super()`, `http2=True`, `stream()`, the second hop of a loopback-to-public redirect, and `httpx2.websocket()`. *Verified untouched:* ASGI, WSGI and Mock transports, and starlette's `TestClient` on `http://testserver` (starlette 1.10 on httpx2, and 1.3.1 on httpx). **Bypasses:** a transport not derived from `HTTPTransport` that does its own I/O (the SDKs' opt-in `DefaultAioHttpClient`, a hand-written `BaseTransport`); `urllib3`, `httpcore` or `httpcore2` called directly (B4); and the hop to a configured proxy, because the decision is on the destination (*verified:* a public proxy with a loopback destination reaches the network with both `requests` and `httpx2`). |
| 3 | Eager or lazy import | **Eager.** `install_guard` imports every installed member. | *Measured:* added to `install_guard` in a process that already imported `arail.egress`: httpx2 about 6.5 ms and httpx about 8.3 ms (cold, alone: about 32 ms each). That is noise next to portal start, and small for the doctor and the worker. Lazy patching would need an import hook, a window before the hook fires, and interplay with httpx2's own `sys.meta_path` alias finder. *Verified:* "install first, import later" is refused in all three venvs (V1). One side effect is kept as an assumption: with both libraries installed, eager import of `httpx` makes a later `httpx2.alias_httpx()` raise (loudly). ARAIL never calls it. |
| 4 | TD51: one place that resolves the library | New stdlib-only module **`src/arail/httpx_family.py`**: `FAMILY`, `is_installed(name)`, `installed()`, `preferred()`. Preference is newest first (httpx2, then httpx), starlette's own order. `preferred()` raises `ModuleNotFoundError` naming both when neither is installed. It never falls back silently past a broken import. **In this PR:** the guard; `open_notebook_seed.py:19` (a real fault on a fresh install: the endpoint raises after `docker compose up`); `doctor.py:278` (lists every member, plus one coverage line). **Filed as TD52:** `tests/test_host_edge.py`, `tests/test_host_edge_brain.py`, `tests/portal/test_opencode_routes.py`, `tests/buddy_conformance.py:346`, and a new find, `tests/test_portal_login.py:235`. `conftest.py:284` belongs to #211. | The test half is harness hygiene on files no workflow runs, and they touch portal-auth paths that #211 is rewriting. *Measured* (CI-style full suite on untouched main, from the session's background run): the 3 files fail to collect; `buddy_conformance.py:346` causes 132 failures in `test_host_edge_conformance.py`; `test_portal_login.py:235` filters the client's request log by the logger name `httpx`, httpx2 logs as `httpx2`, so 1 test fails on the test client's own log line. `test_host_edge_brain.py` must build its client on the library `openai` itself uses, so `preferred()` is the wrong tool there. *Verified:* `open_notebook_seed` uses only `get`, `post` and `put` (with `auth`, `json`, `timeout`), `status_code`, `json()` and `text`. The signatures are identical in httpx2. |
| 5 | Tests that cannot pass vacuously | Parametrize over an **independently computed** installed set (the test helper's own `find_spec` on literal names, cross-checked with `importlib.metadata`), not over `httpx_family.installed()`. There is **no skip for an absent library**: an explicit test asserts each absent member is reported `"absent"`. A **sentinel** fails when neither library is importable. A **leg declaration** `ARAIL_TEST_EXPECT_HTTPX_FAMILY` must equal the installed set; it is required when `CI=true`. The literal brief probe runs in a **fresh interpreter** per installed library (4 variants), along with install-first order, aliasing, an unimportable library and huggingface_hub (never skipped). SDK probes are gated by `ARAIL_TEST_EXPECT_SDKS`. Tripwires: unknown `httpx*`/`httpcore*` modules, unknown transport classes, the documented lower-layer and proxy gaps. Also a docs/status consistency test, an identity check after reset, idempotency over 3 cycles, threads, and caller attribution. Both existing skip sites are rewritten. | B5: the two silent skips were exactly the checks that would have caught TD50. A sentinel that shares the guard's own lookup would agree with a broken lookup, hence the independent set. Fresh interpreters remove shared patch state. The subprocess pins `PYTHONPATH` to this checkout and asserts `arail.egress.__file__`, because the conftest documents an editable install that points at another checkout. |
| 6 | CI | New workflow **`.github/workflows/egress-guard.yml`** with 5 legs, each built from scratch and declared: (1) httpx2 only, pip floating, Python 3.11, plus an SDK step (`pip install anthropic openai`); (2) httpx2 only, pip floating, **Python 3.10** (resolves httpx2 2.14.0); (3) httpx only, `uv sync --frozen --extra dev`; (4) both: pip floating plus `pip install httpx`; (5) **neither: must FAIL its sentinel** (an inverted check). Runs on `pull_request` (no path filter), push to main, **daily** schedule, and `workflow_dispatch`. `permissions: contents: read`, a concurrency group, the 3-attempt install retry, `timeout-minutes: 15`, about 4 to 5 min wall time. A junit check fails on **any skip**, any failure, or a listed file that ran zero tests. It does not depend on #211: no listed file goes through portal auth. | B6: no workflow ran an egress test. CI floats (TD49), so the state can change on PyPI with no commit here. The repo is public (Actions minutes cost nothing), and Blueprint Smoke already runs daily. *Verified:* every listed existing file passes today in both the CI-style and lock venvs (background baseline), apart from the 2 known skips. *Verified:* the same files are green today in a venv with neither library (79 passed, 2 skipped, exit 0), which is exactly the vacuous pass leg 5 turns red. A fresh `maximus` install has **both** libraries (*resolved:* httpx via jupyterlab, langchain-core and langgraph-sdk; httpx2 via anthropic, huggingface-hub and langsmith), so leg 4 is a real user state. It is built deterministically all the same, so ecosystem churn cannot turn it red. |
| 7 | Documentation | Exact wording is in "Documentation" below, for README, PRIVACY (what is enforced, known gaps, the audit grep), the modal, the egress and airgap docstrings and the router comment. **B4 and the proxy hop are documented in this PR** (doc-only, plus documented-gap tripwires, plus `KNOWN_GAPS` moved to `arail.airgap` and named by `/api/airgap/status`). Closing them is TD53 and TD54. The QA doc tests stay green: the modal still says `httpx` and `aiohttp`. `test_qa_airgap_happy_setup.py`'s gap lists are corrected on purpose: `httpx` is no longer a gap. | The brief: the docs must "say what is actually wrapped". The status endpoint is what the modal renders, so it must name the same gaps, plus any library the guard failed to patch at runtime. |
| 8 | BACKLOG | **Agreed:** the closure of TD50 and TD51 and the new TD52 to TD55 are the **last commit, after #211 merges**. Improvement: the builder drafts the exact rows in SPRINT.md now (text below), so the last commit is mechanical. If this PR is ready while #211 is still open, merge it without that commit and land the rows as a ledger-close commit on main once #211 is in (the #208/#210 pattern). Renumber at that point if anything else took TD52 first. | TD49 to TD51 exist only on #211's branch. Editing them here conflicts in either merge order. |

## Restatement

With `LAB_MODE=airgapped`, ARAIL promises that cloud-provider calls are refused at the HTTP layer by an in-process guard, `arail.egress.install_guard()`. The guard patches `requests`, `urllib.request` and one module named `httpx`. huggingface-hub 2.x, anthropic 2.x and openai 3.x (and starlette's test client) moved to `httpx2`, a successor with its own import name. So a fresh install, which has `httpx2` and often no `httpx`, runs its model downloads and SDK calls past the guard. A mixed install guards the library that is not being used. The tests that pinned the guard skipped themselves, and no CI job ran them. This sprint makes the guard cover every installed member of the httpx family, from a single declared list. A family member the guard cannot patch is made loud instead of silent. The proof runs in every library state an install can be in: tests that cannot pass vacuously and a CI workflow whose fifth leg proves the proof can fail. Then README, PRIVACY, the modal and the docstrings say exactly what is wrapped and what is not. The only runtime consumers of "the httpx library" (the Open Notebook seeding and the doctor) move to the same resolver. The test files that import `httpx` by name are filed as debt.

**Non-goals:** wrapping `urllib3`/`httpcore` directly (TD53); checking the proxy hop (TD54); fixing hybrid mode's audit claim (TD55); the test half of TD51 (TD52); TD49 (floating versus lock); anything under `src/arail/deep_*.py`, `src/dac_world/` or `src/arail/buddy_host/contract/`; `tests/conftest.py`; `pyproject.toml`, `uv.lock` and `CHANGELOG.md` (frozen paths of `tests/test_queuellm_rename_frozen_surface.py`); any `aerollm`/`AERO_*` name.

## Assumptions

- **A1.** httpx and httpx2 keep the transport design (`HTTPTransport.handle_request`, `AsyncHTTPTransport.handle_async_request`) within a major version. hf, anthropic and openai pin `httpx2<3`. *Verified* on httpx 0.28.1 and httpx2 2.14.0 and 2.16.0. If a release breaks it, decision 1 makes it loud (`EgressGuardIncomplete`), and the structural tripwire turns the daily CI run red.
- **A2.** Clients look the method up on the class at call time (`Client.send` → `transport.handle_request`). *Verified* for hf (including its shared session built before install), anthropic, openai and redirects. Nothing in tree reloads httpx or httpx2, or keeps a reference to the unpatched method taken before install.
- **A3.** ARAIL does not call `httpx2.alias_httpx()`. An application that embeds ARAIL and wants it must call it before `install_guard`, which is httpx2's own rule. Called after, it raises `RuntimeError` when httpx is installed (*verified*). That is loud, not a hole.
- **A4.** The threat model is unchanged: well-meaning code, in-process only. The DNS lookup `is_local_host` does for non-IP hosts stays as it is (pre-existing, documented).
- **A5.** Python ≥ 3.10. httpx2 ≥ 2.15 requires 3.11, so on 3.10 an unpinned install resolves httpx2 2.14.0 with hf 2.4.0 (*resolved* with `uv pip compile --python-version 3.10`; pip could pick differently, and leg 2's declaration catches that on the first run). *Verified:* the prototype guard and the hf probe behave the same on CPython 3.10.22 with httpx2 2.14.0.
- **A6.** CI keeps floating dependencies (TD49 undecided). The legs model both the floating state and the lock state.
- **A7.** The repo stays public. Scheduled workflows on a public repo are disabled after 60 days with no repository activity, and a scheduled failure notifies whoever last edited the `cron` line.
- **A8.** A fresh `maximus` install has both libraries; a `minimalist` (base) install has httpx2 only; the lock has httpx only (*resolved*, 2026-11-01).
- **A9.** The callers' existing exception handling stays as it is: `portal/app.py:900-906`, `agents/loader.py:313-317`, `doctor.py:79-93`. The nucleus worker (`nucleus/worker.py:39`) has none and fails closed. Decision 1's loudness depends on these. *(Review, 2026-11-01: they did stay as they are, and that is the gap REVIEW.md B1 names: the portal and the loader report and then keep serving with the unpatched library open; only the worker stops, and it prints a raw traceback, N2.)*

## Data flow

```
install_guard()        callers: portal _startup · agents.loader.load_all · nucleus.worker · doctor
  │ RLock
  ├─ requests ........ HTTPAdapter → GuardedHTTPAdapter        (save the original only if not already guarded)
  ├─ urllib.request .. install_opener(guarded HTTP/HTTPS handlers)
  └─ for name in httpx_family.FAMILY  ("httpx2", "httpx"):
        sys.modules[name] or find_spec(name)
          ├─ absent ............................ coverage[name] = "absent"     (nothing here can use it)
          └─ installed → import_module(name)
               ├─ raises ....................... coverage[name] = "unguarded: import raised <Type>: <msg>"
               └─ for (HTTPTransport, handle_request), (AsyncHTTPTransport, handle_async_request):
                     ├─ class/method missing ... coverage[name] = "unguarded: <Class>.<attr> is missing"
                     ├─ already _arail_guarded . skip  (alias_httpx / re-export: the same class object)
                     └─ else ................... _PATCHED[(cls, attr)] = (owned, original); setattr(cls, attr, wrapper)
  ├─ any "unguarded" → _log.error · raise EgressGuardIncomplete   (after everything else is patched; _INSTALLED stays False)
  └─ else _INSTALLED = True

a request through any httpx-family client (sync or async; built before or after install; SDK-internal; module-level get())
  Client.send → per redirect hop → transport.handle_request(request)
     └─ class lookup → ARAIL wrapper → _check_httpx_url(str(request.url))   ← decides on the DESTINATION only
          local (loopback / RFC1918 / link-local) ............. original transport
          allow_egress scope (hybrid only) .................... record_allow → original
          allow_bootstrap_fetch scope + allowlisted host ...... record_allow → original
          airgapped ........................................... record_block → lab/data/egress.jsonl → raise EgressBlocked (no I/O)
          hybrid .............................................. original transport
  ASGITransport / WSGITransport / MockTransport / starlette _TestClientTransport: other classes, untouched

coverage() ─┬─ doctor: the components list names every FAMILY member; one line says what the guard covered
            ├─ /api/airgap/status: known_gaps = airgap.KNOWN_GAPS + one entry per "unguarded" library → the modal
            └─ tests (checked against behaviour, never used as the ground truth)
```

## Interface contracts

### `arail.httpx_family` (new, `src/arail/httpx_family.py`, stdlib only)

| Name | Promises | Requires | On bad input |
|---|---|---|---|
| `FAMILY: tuple[str, ...] = ("httpx2", "httpx")` | The httpx-family import names, newest first. The only list the guard, the doctor, the seed and the docs test read. | None. | Not applicable. Pinned by a test, so changing it is a deliberate act that must also update the docs. |
| `is_installed(name) -> bool` | True if `sys.modules[name]` is a module, or `find_spec(name)` finds one. Never imports. A `None` entry in `sys.modules` counts as absent (importing it would fail anyway). | `name` is a top-level module name. | `ValueError` from `find_spec` (a module with `__spec__ = None`) counts as installed if it is in `sys.modules`, else absent. |
| `installed() -> tuple[str, ...]` | The installed `FAMILY` members, in `FAMILY` order. No import. | None. | Not applicable. |
| `preferred() -> ModuleType` | Imports and returns the first installed member. | At least one installed. | Neither installed: `ModuleNotFoundError(name="httpx2")`, with a message naming every member and saying how to install one. The first member installed but its import raises: that exception propagates. There is **no silent fallback** to the next member. |

### `arail.egress` (changed)

| Name | Promises | Requires | On bad input or failure |
|---|---|---|---|
| `install_guard()` | Patches requests, urllib.request and every installed `FAMILY` member (the data flow above). Idempotent; safe from any thread (RLock). Patches each class at most once, even under two names. | None. | A member installed but unpatchable: everything else is patched, `coverage()` records the reason, the error is logged, then `EgressGuardIncomplete` is raised. `_INSTALLED` stays False, and each later call retries and raises again. Never leaves a member silently unpatched. |
| `_reset_for_tests()` | Restores the original `requests` adapter class and, for every `(cls, attr)` in `_PATCHED`, the exact pre-install state (`setattr` if owned, `delattr` if inherited). Clears `coverage()`. `_INSTALLED = False`. **Never raises** (`conftest._reset_egress_guard` swallows exceptions, so a raising reset would pollute later tests silently). | Test use only. | A `delattr` on an attribute that is already gone is ignored. |
| `coverage() -> dict[str, str]` | Keys `"requests"`, `"urllib.request"`, then `FAMILY` in order. Values are `"guarded"`, `"absent"`, or `"unguarded: <reason>"`. `{}` before the first install and after a reset. Reflects the last install attempt, including one that raised. | None. | Not applicable. |
| `uncovered() -> dict[str, str]` | `{name: reason}` for the `"unguarded"` entries. | None. | Not applicable. |
| `GUARDED_CLIENTS: tuple[str, ...]` | `("requests", "urllib.request", *FAMILY)`: what the docs must name. | None. | Not applicable. |
| `class EgressGuardIncomplete(RuntimeError)` | `.uncovered` holds the same dict as `uncovered()`. The message names each library, the reason, the fact that its requests are **not** checked against `LAB_MODE`, what *is* guarded, and the fix ("install a supported version (httpx2<3, httpx<1) or uninstall it"). It is not an `EgressBlocked`: no request was refused. | None. | Not applicable. |
| wrappers `_guarded_handle_request(self, request, *args, **kwargs)` / `async _guarded_handle_async_request(...)` | Run `_check_httpx_url(str(request.url))` first, then delegate with the same arguments. They carry `_arail_guarded = True` and `__wrapped__ = original`. They do **not** use `functools.wraps`, so introspection shows ARAIL's wrapper. | `request.url` stringifies to the destination URL. | `EgressBlocked` per the decision tree; any exception from the original propagates unchanged. |
| `_current_caller()` | First frame whose module is not a guard frame. A guard frame's module equals an entry of `_SKIP_MODULES` or starts with entry + `"."`. `_SKIP_MODULES` names `*FAMILY`, `"httpcore"` and `"httpcore2"` explicitly. httpcore frames are never on the stack at check time; they are listed so the set names the family on purpose. | None. | `"unknown"` as before. |
| unchanged | `allow_egress`, `allow_bootstrap_fetch`, `record_block`, `record_allow`, `read_recent_blocks`, `probe_internet`, `GuardedHTTPAdapter`, the urllib handlers, `_check_egress_or_raise`. The audit schema is unchanged. | | |

Renamed private helpers (nothing outside `egress.py` references them; checked with grep): `_install_httpx_guard` → `_install_httpx_family_guard`; `_reset_httpx_guard` → `_reset_httpx_family_guard`. `_ORIG_HTTPX_SYNC/ASYNC` → `_PATCHED`. `_check_httpx_url` keeps its name (its docstring now says "httpx-family requests").

### `arail.airgap`

- New `KNOWN_GAPS: tuple[str, ...]`. The three strings `/api/airgap/status` lists today stay byte-identical, then two new entries: direct `urllib3`/`httpcore`/`httpcore2`, and the proxy hop. It moves here from `portal/app.py` for the reason `AIRGAPPED_NOTICE` did: a test can read it without booting the portal.
- The module docstring is corrected (see "Documentation").

### `/api/airgap/status` (`portal/app.py`)

- `known_gaps = [*airgap.KNOWN_GAPS, *(f"{name}: installed, but the guard could not patch it ({why}); its requests are NOT checked" for name, why in egress.uncovered().items())]`.
- `guard_installed` stays `egress._INSTALLED`. It is therefore False after an incomplete install, which is honest. No new field, and no other change to the endpoint.

### `arail.doctor`

- `check_components`: the version list is `("fastapi", "uvicorn", "lancedb", "pydantic", "requests", *httpx_family.FAMILY, "neo4j")`.
- `check_environment`: on success, `egress guard      : installed — guards requests, urllib.request, httpx2 (not installed: httpx)`, built from `coverage()`. On `EgressGuardIncomplete`, the existing `except` branch prints it and records `egress_guard` required with `ok=False`, so the doctor's exit code degrades.

### `arail.open_notebook_seed`

- `httpx = httpx_family.preferred()  # noqa-airgap: localhost-only (...)` replaces `import httpx` at line 19. Nothing else changes. It still fails at import when neither library is installed, now with a message naming both.

### Test support (new, not collected by pytest)

- `tests/egress_family_support.py` (stdlib plus `arail.httpx_family` only, so that the neither leg can import it): `LITERAL_FAMILY = ("httpx2", "httpx")`; `installed_independently()` (its own `find_spec` on the literal names, cross-checked with `importlib.metadata.version`); `declared_family()` (parses `ARAIL_TEST_EXPECT_HTTPX_FAMILY`: comma-separated members or the literal `none`; an empty value or an unknown name fails with a message); `PROBE_URL = "http://egress-probe.invalid/"`; and `run_fresh(code, *args, tmp_path, extra_env=None, timeout=60) -> dict`. `run_fresh` runs `sys.executable -c code` with `cwd=tmp_path` and env = os.environ with `ARAIL_MODE`, `HF_HUB_OFFLINE` and every `*_proxy` removed, and `LAB_MODE=airgapped`, `ARAIL_DATA_DIR`, `HF_HOME`, `HF_HUB_DISABLE_TELEMETRY=1` and `ARAIL_ENV_FILE=<tmp>/none.env` set. `PYTHONPATH` is prefixed with this checkout's `src`, and `-I` is never used. It asserts exit 0, parses the last stdout line as JSON, and asserts `Path(result["egress_file"])` is inside this checkout's `src`.
- `tests/egress_ci_check.py`: `no-skips JUNIT FILE...` (exit 0 only if there is no skipped, failed or errored case and every FILE contributed at least 1 case) and `negative-control JUNIT RC` (exit 0 only if `RC == 1`, the sentinel case failed with `UNPROVEN` in its message, and the declaration case passed). It prints one line per finding: what it means and where to look.

### CI contract (`.github/workflows/egress-guard.yml`)

The leg names are part of the contract once anyone marks them required. A leg is red when any of these is true: a test fails; any test skips; a listed file ran zero tests; the installed family differs from the leg's declaration; the install fails 3 times; or, in leg 5, the sentinel does **not** fail as specified.

## Failure modes

| # | Failure | Detection | Recovery | Tests |
|---|---|---|---|---|
| F1 | A family member is installed and not patched (the TD50 class: list edited, import path changed, guard reverted) | The literal brief probe in a fresh interpreter, per installed library; in-process tests; a CI leg per state | The guard patches every installed `FAMILY` member. Red output names the library and the variant. | FR-1, FR-2, EG-1 to EG-4, BY-1 |
| F2 | A member is importable but unpatchable (a class or method is missing or renamed, or the import raises) | `EgressGuardIncomplete`; `coverage()`; the doctor's required check; status `known_gaps`; portal activity "Egress guard install failed" | Everything else stays patched. The operator reinstalls a supported version or uninstalls it. | EG-20, FR-4, DR-3, DOC-5 |
| F3 | A new family module appears (`httpx3`, a fork with a numbered name) | The name-scan tripwire turns red (daily) | Add it to `FAMILY`, the docs and the literal list | EG-23 (plus a planted-module canary in Evidence E10) |
| F4 | A covered library adds a network transport not derived from `HTTPTransport` | The structural tripwire (subclass scan plus exported `*Transport` names) | Extend the patch targets | EG-24 |
| F5 | Double wrap via `alias_httpx()` or a re-export: two checks, two audit lines, a reset that leaves a wrapper | Class-keyed state plus the marker | Wrap once; restore exactly | EG-18, FR-3 |
| F6 | Reset does not restore, so patch state leaks across tests and files (pytest ordering) | An identity check after reset; 3 install/reset cycles; order-independence runs | `_PATCHED` restore with owned/inherited; the per-module autouse reset before and after each test | EG-16, EG-17, E13 |
| F7 | A retried install after an incomplete one overwrites the saved `requests` adapter (*verified* latent bug) | The test: incomplete install twice, then reset, then `requests.adapters.HTTPAdapter` is the original | Save the original only if the current class is not `GuardedHTTPAdapter` | EG-20 |
| F8 | Audit `caller` attribution relies on the startswith accident; a module like `httpxtools` would be skipped by mistake | Synthetic-frame tests; caller asserted in blocked-request tests | Exact or dotted-prefix match; explicit entries | EG-21, EG-22, EG-1 |
| F9 | A test passes vacuously: neither library; a skipped library; a leg that lost its library; the subprocess imports another checkout; a file collected nothing | The sentinel; zero-skip junit check; leg declaration; `egress_file` assertion; per-file count | Red, with a message saying what is unproven | EG-0, EG-0b, run_fresh's assertion, CI-1 to CI-4, leg 5 |
| F10 | A regression only for clients built before install, only async, or only module-level helpers | Variants in the fresh probe and in-process | Not applicable | FR-1 (4 variants), EG-3, EG-4 |
| F11 | Import order: `install_guard` runs before anything imports the library | Fresh "install first" test | Eager import | FR-2 |
| F12 | huggingface_hub downloads not stopped, or retried with backoff | hf probe: final `EgressBlocked`, exactly 1 refusal per call | Not applicable (hf does not retry a `RuntimeError`) | FR-5 |
| F13 | An SDK's default client moves off a guarded transport (for example, aiohttp by default) | SDK probe in leg 1's SDK step (daily) | Decide: extend the guard or document it | SDK-1 |
| F14 | In-process transports blocked by mistake (`TestClient`, ASGI, WSGI, Mock) | Test with the guard installed, airgapped, `http://testserver` | They are separate classes; nothing to patch | EG-13 |
| F15 | A loopback-to-public redirect: the second hop leaves | Mount-based redirect test (no port bound) | Each hop calls `handle_request` | EG-10 |
| F16 | A proxy at loopback carries a public request | Proxied-client tests (`proxy=`, `mounts=`, env proxy) | The decision is on the destination | EG-11 |
| F17 | A public proxy carries a request for a local destination (*verified* gap) | Documented-gap tripwire (fails if the gap is closed, prompting a doc update) | Documented now; fix is TD54 | BY-4 |
| F18 | `urllib3`, `httpcore` or `httpcore2` called directly | Documented-gap tripwires | Documented; fix is TD53 | BY-2, BY-3 |
| F19 | Docs drift from code (a member added without docs, or a doc claiming coverage that is not there) | Doc/status consistency test, driven by `GUARDED_CLIENTS` and `KNOWN_GAPS` | Not applicable | DOC-1 to DOC-4 |
| F20 | `EgressGuardIncomplete` is swallowed by the portal, so the user never sees it | Status `known_gaps` names the library; `guard_installed` false; the doctor fails | Not applicable | DOC-5, DR-3 |
| F21 | Threads: the patch misses a worker thread, or two concurrent installs double-wrap | Worker-thread refusal; 8 concurrent installs leave one wrap | Process-wide class patch; RLock | EG-14, EG-19 |
| F22 | New code is incompatible with Python 3.10 | Leg 2 | Not applicable | CI leg 2 |
| F23 | Ecosystem drift changes a leg's state (hf drops httpx2, a lock bump) | Leg declaration; daily schedule | Someone updates the declaration consciously | EG-0b, schedule |
| F24 | The negative-control leg passes for the wrong reason (collection error, nothing collected) | `negative-control` check: rc == 1, sentinel failed with `UNPROVEN`, declaration passed | Not applicable | CI-4, leg 5 |
| F25 | `open_notebook_seed` fails on an httpx2-only install (TD51 runtime) | Import plus a loopback API call in every leg | Resolver | ON-1, ON-2 |
| F26 | The doctor says "httpx: not installed" while httpx2 is the guarded library | Doctor output tests | List every member; coverage line | DR-1, DR-2 |
| F27 | A secret in a URL leaks through the new path (audit line or exception text) | Blocked request with `?token=` | Host-only audit (unchanged code path) | EG-1 (security assertion) |
| F28 | Wider-suite regression: a test that boots the portal (which installs the guard) and then makes an httpx2 call to a public host now gets `EgressBlocked` | Full-suite before/after comparison per venv (QA) | Conftest's autouse reset after each test (exists); fix the test, not the guard | E8 |
| F29 | A broken but installed library (import raises) causes a false alarm | `EgressGuardIncomplete` names the import error | By design: fail closed. The message says how to fix it. | FR-4 |

## Test strategy

Rules for every new or rewritten test:

- **No `importorskip` and no skip for the thing under test.** Parametrize over `installed_independently()`. A library that is absent gets an explicit assertion (EG-15), never a skip.
- **No real host is ever contacted.** Every URL expected to be blocked uses a `.invalid` host (RFC 6761: never resolves), so a regression shows as a fast `ConnectError`, never a real connection. In-process tests also monkeypatch `socket.gethostbyname` to return a globally routable address (`151.101.64.81`, `tests/egress_family_support.PUBLIC_TEST_IP`) for names, so the local check is deterministic and makes no DNS query. *(Erratum, review 2026-11-01, AF-1: this said the TEST-NET-3 address `203.0.113.7`. `ipaddress` calls every documentation range private (`203.0.113.7`, `198.51.100.7`, `192.0.2.7` are all `is_private` on 3.10.22 and 3.11.17), and the guard treats a private address as local, so no `EgressBlocked` could ever be seen. Only `gethostbyname` is patched; `getaddrinfo` stays real, so a regression is still a `.invalid` `ConnectError`, never a connection to that address.)* The fresh-interpreter probe keeps real resolution, because that is the brief's literal probe.
- **Pass-through cases** stub the original transport method with a `ReachedTransport` marker before install (the existing pattern), never a live network.
- **State:** each new in-process module has an autouse fixture that calls `egress._reset_for_tests()` before and after each test (in addition to conftest's).
- **No fixed port.** The one loopback server (ON-2) binds port 0. All other tests bind nothing.
- The test modules that leg 5 imports (`tests/test_egress_httpx.py`, `tests/egress_family_support.py`) import **no third-party HTTP library at module level**, and no huggingface_hub.

### Unit and in-process

`tests/test_httpx_family.py`: commit 1 adds the resolver tests; commit 5 adds the open_notebook_seed and doctor tests.
- HF-1 `FAMILY == ("httpx2", "httpx")`. This pins the order and content; a change must update docs and tests on purpose.
- HF-2 `installed()` equals the independent lookup.
- HF-3 `preferred().__name__ == installed()[0]`.
- HF-4 With neither installed (simulated with `monkeypatch.setitem(sys.modules, name, None)` for both), `preferred()` raises `ModuleNotFoundError` naming both.
- HF-5 A `None` entry in `sys.modules` counts as absent.
- HF-6 A first member whose import raises propagates; there is no fallback.
- ON-1 `arail.open_notebook_seed` imports in every leg, and `seed.httpx.__name__ == installed()[0]`.
- ON-2 `_wait_for_api(port, retries=1, delay=0)` returns True against a port-0 loopback server answering 200. That proves the seeding code works on httpx2 for real.
- DR-1 `check_components` prints every `FAMILY` member (version or "not installed"). Monkeypatch `shutil.which` to return None, so no ollama subprocess runs.
- DR-2 `check_environment`'s egress line names each `"guarded"` entry of `coverage()`, and records `egress_guard` with `ok=True`.
- DR-3 With `AsyncHTTPTransport` deleted from an installed member (`monkeypatch.delattr` on the module attribute), `egress_guard` is recorded with `ok=False` and its detail names the library.

`tests/test_egress_httpx.py`: rewritten in commit 2; the tripwires come in commit 4. `[lib]` means parametrized over the independent installed set.
- EG-0 **Sentinel** `test_httpx_family_coverage_is_provable_in_this_environment`. Fails with `"egress coverage of the httpx family is UNPROVEN in this environment: neither httpx2 nor httpx is importable, so no test here exercised the guard on them. Install one (CI: see .github/workflows/egress-guard.yml)."`
- EG-0b **Declaration** `test_installed_httpx_family_matches_the_ci_leg_declaration`. When `ARAIL_TEST_EXPECT_HTTPX_FAMILY` is set, the independent installed set must equal it. When it is unset and `CI == "true"`, fail ("every CI run of the egress tests must declare its libraries"). When it is unset locally, skip (it is a CI-leg check, not the guard). It never skips in CI.
- EG-1[lib] Airgapped `Client().get("http://egress-probe.invalid/v1?token=SECRET_DO_NOT_LEAK")` raises `EgressBlocked`. Its audit line has `url_host == "egress-probe.invalid"`, `reason == "airgapped"`, and `caller ==` this test's module plus qualname (attribution on purpose). Keep these tests as module-level functions: on Python 3.10 `_current_caller` falls back to `co_name`, which equals the qualname only outside a class. `SECRET_DO_NOT_LEAK` appears neither in `egress.jsonl` nor in `str(exc)`.
- EG-2[lib] The same, async. EG-3[lib] Clients built before install, sync and async. EG-4[lib] Module-level `lib.get()`.
- EG-5[lib] `127.0.0.1` and `10.0.0.5` pass, sync and async (stub marker).
- EG-6[lib] Hybrid passes a public request.
- EG-7[lib] Hybrid plus `allow_egress` passes and writes `allow:<reason>`.
- EG-8[lib] Airgapped `allow_egress` raises on entry, and the request inside is never made (the ratchet, unchanged).
- EG-9[lib] Airgapped `allow_bootstrap_fetch` with an approved consent (the `test_egress_bootstrap.py` pattern): an allowlisted host reaches the stub with an `allow:` line; a non-listed `.invalid` host is refused.
- EG-10[lib] Redirect: `mounts={"http://127.0.0.1": MockTransport(302 → http://egress-probe.invalid/next)}` with `follow_redirects=True`. The second hop, through the default transport, is refused, sync and async (*verified*, no port).
- EG-11[lib] `proxy="http://127.0.0.1:9"`, `mounts={"all://": HTTPTransport(proxy=...)}` and env `HTTPS_PROXY`/`HTTP_PROXY` (`monkeypatch.setenv`) are all refused. socks and `http2=True` need `socksio`/`h2`, which are not in `dev`, so they are QA evidence (E5).
- EG-12[lib] `transport=HTTPTransport(retries=2)`, and a subclass whose `handle_request` calls `super()`, are refused.
- EG-13[lib] Airgapped with the guard installed: ASGI, WSGI and Mock transports on `http://testserver`, and `starlette.testclient.TestClient(app).get("/")` on a two-line app, all succeed.
- EG-14[lib] A request from a `threading.Thread` is refused.
- EG-15 For each `LITERAL_FAMILY` member not installed, after install, `coverage()[name] == "absent"` and `find_spec(name) is None`. In leg 4 this asserts the absent set is empty. It never skips.
- EG-16[lib] After install and then reset, `vars(cls)[attr]` has no `_arail_guarded`, its `__qualname__` is `HTTPTransport.handle_request` (or the async one), and its `__module__` starts with `lib`.
- EG-17[lib] 3 install/reset cycles. A repeated install leaves the same function object. `__wrapped__` is the library's own function, never a wrapper. One refusal writes exactly one audit line.
- EG-18[lib] Call the per-module patch step directly on a `types.ModuleType` that re-exports `lib`'s classes. It adds no `_PATCHED` entries and reports `"guarded"`.
- EG-19[lib] 8 threads call `install_guard` concurrently. Afterwards `__wrapped__` is the library function.
- EG-20[lib] With `monkeypatch.delattr(lib_module, "AsyncHTTPTransport")`, in airgapped **and** hybrid: `EgressGuardIncomplete`; `coverage()[lib]` starts with `"unguarded"`; `coverage()["requests"] == "guarded"`; `lib.HTTPTransport.handle_request` is guarded; `_INSTALLED is False`. A second install raises again. After `_reset_for_tests()`, `requests.adapters.HTTPAdapter is` the class saved before the test (F7).
- EG-21 `_current_caller` called through an exec'd function whose `__name__` is `httpx2._transports.default`, `httpx._client`, `httpcore2._sync.connection_pool`, `httpcore._async`, `requests.sessions`, `urllib3.connectionpool` or `arail.egress` returns this test. For `httpxfoo`, `httpx2extra` or `anthropicx`, it returns that frame.
- EG-22 `{"httpx", "httpx2", "httpcore", "httpcore2"} <= egress._SKIP_MODULES`.
- EG-23 **Tripwire:** no top-level module from `pkgutil.iter_modules()` matches `httpx\d*` outside `FAMILY`, or `httpcore\d*` outside `("httpcore", "httpcore2")`. The message says to add it to `FAMILY` and the docs, or to the documented lower layers.
- EG-24[lib] **Tripwire:** after importing `lib` (and `lib.websockets` if it imports), every loaded subclass of `lib.BaseTransport`/`lib.AsyncBaseTransport` defined under `lib` is a subclass of `HTTPTransport`/`AsyncHTTPTransport` (guarded) or `ASGITransport`/`WSGITransport`/`MockTransport` (in-process). The exported `*Transport` names are a subset of the 7 known today.

`tests/test_qa_airgap_bypass_attempts.py`:
- BY-1[lib] (commit 2) `test_httpx_is_guarded_in_airgapped` is parametrized; the skip at line 214 is gone. The docstrings of the module and of `TestDocumentedGaps` name httpx and httpx2 as wrapped.
- BY-2, BY-3[core], BY-4 (commit 4) are documented-gap tripwires in `TestDocumentedGaps`. BY-2: `urllib3.PoolManager(retries=False).request("GET", PROBE_URL)` raises something that is not `EgressBlocked` and writes no audit line. BY-3[core] (over installed `httpcore`/`httpcore2`): the same for `core.request`. BY-4[client] (over `requests` and installed family members): a destination of `127.0.0.1:9` through a proxy at `http://egress-probe.invalid:8080` raises something other than `EgressBlocked`. Each failure message reads "DOCUMENTED-GAP TRIPWIRE ... update docs/PRIVACY.md, README, the modal and airgap.KNOWN_GAPS (TD53 / TD54)".

### Integration (fresh interpreter)

`tests/test_egress_httpx_fresh.py` (commit 3):
- FR-1[lib] **The brief's probe.** Import `lib`; build `Client(timeout=3)` and `AsyncClient(timeout=3)`; set `LAB_MODE=airgapped`; `arail.egress.install_guard()`; then `lib.Client(timeout=3).get("http://egress-probe.invalid/")` (sync-after, the literal probe). Also sync-before, async-after and async-before. All four end in `EgressBlocked`; there are 4 audit lines with `url_host == "egress-probe.invalid"`; `coverage()[lib] == "guarded"`; `egress_file` is inside this checkout.
- FR-2[lib] `install_guard()` runs while `lib not in sys.modules`; then import, build and request, sync and async. All are refused.
- FR-3[lib] `sys.modules[other] = lib_module` (what `alias_httpx()` does; also a stand-in for a module re-exporting another's classes) before install. One wrap (2 `_PATCHED` entries); both names report `"guarded"`; one refusal gives one audit line; after reset the originals are back.
- FR-4 A `sys.meta_path` finder makes `import <FAMILY[0]>` raise `RuntimeError`. The result is `EgressGuardIncomplete`; `coverage()[FAMILY[0]]` starts with `"unguarded: import raised RuntimeError"`; `requests` is guarded; `_INSTALLED` is False. Not parametrized: it is simulated and works in every leg.
- FR-5 **huggingface_hub** (a core dependency, never skipped). Calls: `HfApi(endpoint=E).model_info("a/b")`, `hf_hub_download("a/b", "f.txt", endpoint=E, cache_dir=tmp)`, `snapshot_download("a/b", endpoint=E, cache_dir=tmp)`, and `model_info` through a `huggingface_hub.utils.get_session()` built **before** install. Each ends with a final exception of `EgressBlocked` and exactly **1** new audit line (no retry). The test reports the hf version and the seconds taken.

`tests/test_egress_sdk_clients.py` (commit 3; runs in CI only in leg 1's SDK step):
- SDK-1[anthropic|openai] Build the default client with `base_url=http://egress-probe.invalid` and a dummy key, before and after install. With `max_retries=0`, the exception chain contains `EgressBlocked`, contains none of `ConnectError`, `ConnectTimeout`, `gaierror` or `NameResolutionError`, and exactly 1 audit line. With default retries, the chain contains `EgressBlocked` and there is at least 1 audit line; the count and the seconds are reported. If an SDK is not installed: fail if `ARAIL_TEST_EXPECT_SDKS` names it, otherwise skip (the SDKs are optional installs). The SDK step's junit check forbids skips.

### Regression

- B5's two skip sites: BY-1 and the rewritten `tests/test_egress_httpx.py`.
- B1: FR-1.
- B2: FR-5.
- The latent `requests`-adapter bug: EG-20.
- The startswith accident: EG-21 and EG-22.
- `tests/test_egress_docs.py` (commit 6):
  - DOC-1 README's "What local-first means" section, PRIVACY's "Known gaps" section and the modal's gap paragraph each name every entry of `egress.GUARDED_CLIENTS` (as `` `name` `` or `<code>name</code>`).
  - DOC-2 PRIVACY, the modal and `" ".join(airgap.KNOWN_GAPS)` name `aiohttp`, `urllib3`, `httpcore` and the proxy hop.
  - DOC-3 No doc or docstring says httpx is not wrapped (regex `httpx\w*\W+(—|-)+\s*not wrapped` over `airgap.__doc__`, PRIVACY and README).
  - DOC-4 `egress.__doc__` names every `FAMILY` member.
  - DOC-5 After a simulated incomplete install (`delattr` as in EG-20), `asyncio.run(portal_app.get_airgap_status())` (called directly, with no TestClient and therefore no auth dependency) returns `guard_installed is False` and `known_gaps` with every `KNOWN_GAPS` string plus one entry naming the library.
- `tests/test_qa_airgap_happy_setup.py` (commit 6, a deliberate edit logged in BUILD_LOG): `test_privacy_md_has_known_gaps_section` checks `("aiohttp", "subprocess", "socket", "urllib3", "httpcore")`; `test_modal_lists_known_gaps` checks `("aiohttp", "urllib3", "httpcore")`. Each gets a comment: "httpx and httpx2 are wrapped now; tests/test_egress_docs.py pins that". The test names are unchanged.
- Builder runs in every commit that touches a non-test file: `tests/test_queuellm_rename_frozen_surface.py`. Its checks: no `AERO_*` or frozen-id tokens lost in modified files, and no frozen path touched.

### CI glue

`tests/test_egress_ci_check.py` (commit 7) feeds synthetic junit XML to `tests/egress_ci_check.py`:
- CI-1 a clean run passes `no-skips`.
- CI-2 a skipped case fails it.
- CI-3 a listed file with zero cases fails it.
- CI-4 `negative-control` passes only with rc 1, the `UNPROVEN` failure and a passing declaration. It fails on rc 0, rc 2 and rc 5, and when the sentinel passed.

The checker that guards against vacuity can itself fail.

### Performance

No per-request benchmark: the wrapper is the same code path httpx already ran (one function call, `urlparse`, `is_local_host`). Report the import cost added to `install_guard` (Evidence E11). Report the refusal latency from FR-1 and FR-5 (milliseconds; a refusal never waits for a network timeout).

### Security

- EG-1's secret assertion.
- EG-8, the ratchet: `allow_egress` cannot open airgapped mode for the new library.
- EG-9: the bootstrap scope is host-allowlisted for the new library.
- F2 is fail-closed.
- No new dependency is added. `anthropic` and `openai` are installed only in leg 1's SDK step, to probe them.

## CI design

The builder copies this and keeps its structure. Comments explain *why*, as in the repo's other workflows.

```yaml
name: Egress Guard

# The airgap invariant (CLAUDE.md: LAB_MODE=airgapped "Don't relax this default") proven in every
# httpx-family state an ARAIL install can be in. TD50: the guard patched `httpx` only, huggingface-hub
# 2.x / anthropic 2.x / openai 3.x moved to `httpx2`, the tests that pinned the guard skipped themselves
# where `httpx` was absent, and no workflow ran them, so nothing turned red.
# Each leg builds ONE state from scratch and DECLARES it (ARAIL_TEST_EXPECT_HTTPX_FAMILY): a leg that
# silently loses a library fails instead of testing less. A skipped test fails the leg (a skip is how
# TD50 stayed hidden). The last leg has neither library and must FAIL its sentinel: the proof that the
# sentinel cannot pass vacuously. No `|| true`, no continue-on-error.
# Daily because CI installs float (TD49): the state can change on PyPI with no commit here.
# No listed test goes through portal auth, so this workflow does not depend on #211 (TD48).

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "41 6 * * *"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: egress-guard-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

env:
  ARAIL_NONINTERACTIVE: "1"
  ARAIL_SKIP_OLLAMA: "1"
  EGRESS_TESTS: >-
    tests/test_httpx_family.py tests/test_egress_httpx.py tests/test_egress_httpx_fresh.py
    tests/test_egress_docs.py tests/test_egress_ci_check.py tests/test_egress_guard.py
    tests/test_egress_bootstrap.py tests/test_qa_airgap_bypass_attempts.py
    tests/test_qa_tier1_egress_guard.py tests/test_airgap_helpers.py
    tests/test_qa_airgap_happy_setup.py tests/test_qa_airgap_ipv6_edge_cases.py
    tests/test_airgap_modal_dom.py

jobs:
  guard:
    name: "Egress guard: ${{ matrix.leg }}"
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        include:
          - { leg: "httpx2 only (pip, floating)",             python: "3.11", install: floating, expect: "httpx2",       sdks: "anthropic,openai" }
          - { leg: "httpx2 only (pip, floating, Python 3.10)", python: "3.10", install: floating, expect: "httpx2",       sdks: "" }
          - { leg: "httpx only (uv.lock)",                     python: "3.11", install: lock,     expect: "httpx",        sdks: "" }
          - { leg: "both (pip, floating + httpx)",             python: "3.11", install: both,     expect: "httpx,httpx2", sdks: "" }
          - { leg: "neither (must fail its sentinel)",         python: "3.11", install: neither,  expect: "none",         sdks: "" }
    env:
      ARAIL_TEST_EXPECT_HTTPX_FAMILY: ${{ matrix.expect }}
    steps:
      - name: Check out repository
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
          cache: pip
      # A fresh unpinned install, what users and the other workflows get. Retry for PyPI transients.
      - name: Install (pip, floating)
        if: matrix.install != 'lock'
        shell: bash
        run: |
          set -euo pipefail
          python -m venv .venv
          .venv/bin/python -m pip install --upgrade pip
          for attempt in 1 2 3; do
            if .venv/bin/pip install -e ".[dev]"; then break; fi
            if [ "$attempt" = 3 ]; then echo "::error::dependency install failed after 3 attempts"; exit 1; fi
            echo "::warning::dependency install attempt ${attempt}/3 failed; retrying in $((attempt * 20))s"
            sleep $((attempt * 20))
          done
          case "${{ matrix.install }}" in
            both)    .venv/bin/pip install httpx ;;
            neither) .venv/bin/pip uninstall -y httpx httpcore httpx2 httpcore2 ;;
          esac
      # What local development follows (uv.lock pins httpx 0.28.1 and no httpx2).
      - name: Install (uv.lock)
        if: matrix.install == 'lock'
        shell: bash
        env:
          UV_PYTHON_DOWNLOADS: never
        run: |
          set -euo pipefail
          python -m pip install --upgrade pip "uv>=0.8"
          for attempt in 1 2 3; do
            if uv sync --frozen --extra dev --python "$(command -v python)"; then break; fi
            if [ "$attempt" = 3 ]; then echo "::error::uv sync failed after 3 attempts"; exit 1; fi
            echo "::warning::uv sync attempt ${attempt}/3 failed; retrying in $((attempt * 20))s"
            sleep $((attempt * 20))
          done
      - name: What this leg installed
        shell: bash
        run: |
          .venv/bin/python - <<'PY'
          import importlib.metadata as md, sys
          print("python", sys.version.split()[0])
          for n in ("httpx", "httpx2", "httpcore", "httpcore2", "huggingface-hub", "starlette", "anthropic", "openai"):
              try:
                  v = md.version(n)
              except md.PackageNotFoundError:
                  v = "not installed"
              print(f"  {n:16} {v}")
          PY
      - name: Egress tests (no failure, no skip, every file ran)
        if: matrix.install != 'neither'
        shell: bash
        run: |
          set -euo pipefail
          .venv/bin/python -m pytest $EGRESS_TESTS -p no:cacheprovider -q -rs --junitxml=egress-junit.xml
          .venv/bin/python tests/egress_ci_check.py no-skips egress-junit.xml $EGRESS_TESTS
      - name: Cloud SDK default clients are refused (anthropic, openai)
        if: matrix.sdks != ''
        shell: bash
        env:
          ARAIL_TEST_EXPECT_SDKS: ${{ matrix.sdks }}
        run: |
          set -euo pipefail
          for attempt in 1 2 3; do
            if .venv/bin/pip install anthropic openai; then break; fi
            if [ "$attempt" = 3 ]; then echo "::error::SDK install failed after 3 attempts"; exit 1; fi
            sleep $((attempt * 20))
          done
          .venv/bin/python -m pytest tests/test_egress_sdk_clients.py -p no:cacheprovider -q -rs --junitxml=sdk-junit.xml
          .venv/bin/python tests/egress_ci_check.py no-skips sdk-junit.xml tests/test_egress_sdk_clients.py
      # The sentinel must FAIL here (exit 1, its UNPROVEN message) and the declaration must PASS
      # (this venv really has neither). Anything else, including a collection error, is red.
      - name: Negative control (neither library) - the sentinel must fail
        if: matrix.install == 'neither'
        shell: bash
        run: |
          set +e
          .venv/bin/python -m pytest \
            "tests/test_egress_httpx.py::test_httpx_family_coverage_is_provable_in_this_environment" \
            "tests/test_egress_httpx.py::test_installed_httpx_family_matches_the_ci_leg_declaration" \
            -p no:cacheprovider -q -rA --junitxml=neither-junit.xml
          rc=$?
          set -e
          .venv/bin/python tests/egress_ci_check.py negative-control neither-junit.xml "$rc"
```

- **Runtime budget:** about 1 to 2.5 min to install, under 1 min of tests (the existing list runs in about 3 to 5 s, and the fresh-interpreter probes take about 1 s each). About 4 to 5 min wall time with the legs in parallel, against a 15-min timeout.
- **What makes it red:** listed in "CI contract" above.
- **Before marking anything required:** check on the PR's first run that leg 2 (Python 3.10, pip) really resolves httpx2. uv says httpx2 2.14.0; if pip differs, the declaration test says so, and the builder records the decision.
- The deploy-key step the other workflows carry is not needed. `pyproject.toml` has no private dependency, and `nucleus-tests.yml` installs `.[dev]` without SSH.

## Documentation (exact wording)

**`README.md` lines 298-307** (the first paragraph of "What "local-first" means") becomes:

```
By default `LAB_MODE=airgapped` — agents in the lab cannot collect
information from the public internet. Calls to loopback and your private
network still work, so a LAN GPU box (Ollama, vLLM, a QueueLLM node)
keeps inferring without changes. Cloud-provider APIs are blocked at the
HTTP layer: the lab's Python-level guard wraps `requests`,
`urllib.request`, `httpx` and its successor `httpx2`, and so everything
built on them, such as the Hugging Face downloader and the Anthropic and
OpenAI SDKs. The dashboard's **Airgapped** badge is clickable — it shows
what is and isn't enforced, the recent blocks, and the known gaps the
guard doesn't cover (`aiohttp`, `urllib3` or `httpcore` used directly, a
proxy you configure, raw sockets, subprocess `curl`; the full list is in
[docs/PRIVACY.md](docs/PRIVACY.md)). The threat model is well-meaning
agent code, not an adversary on this host — for that, run a host
firewall.
```

**`docs/PRIVACY.md` lines 43-44** become:

```
`LAB_MODE=airgapped` (the default) blocks calls made through
`requests`, `urllib.request`, `httpx` or `httpx2` to anything that isn't:
```

**`docs/PRIVACY.md` lines 57-73** ("Known gaps") become:

```
### Known gaps — what the Python-level guard does NOT catch

The guard wraps four client libraries: `requests` (the adapter every new
`Session` gets), `urllib.request` (the default opener), and the httpx
family — `httpx` and its successor `httpx2`, a separate package with its
own import name that huggingface-hub 2.x, anthropic 2.x and openai 3.x use
in place of `httpx`. The httpx-family check sits on the transport classes,
so it also covers clients built before the guard was installed and the
clients SDKs build for themselves: Hugging Face downloads
(`hf_hub_download`, `HfApi`), the default anthropic and openai clients, and
the Open Notebook seeding. `./arailctl doctor` prints which of the four
this lab's guard covered. If one of them is installed but the guard cannot
patch it (a future release renamed its transport classes, say), requests
made through that library are not checked, and the lab says so instead of
staying silent: the portal's activity log records "Egress guard install
failed", `./arailctl doctor` fails its egress check, and the Network Policy
modal lists the library under known gaps.

It does NOT wrap:

- `aiohttp` — not used in tree today. The anthropic and openai SDKs offer
  an opt-in aiohttp client (`DefaultAioHttpClient`); it is not covered.
- `urllib3`, `httpcore` or `httpcore2` called directly — the guard sits one
  layer up, in `requests` and the httpx family. Nothing in `src/arail/`
  calls them directly today.
- The hop to a proxy you configure (`HTTP_PROXY`/`HTTPS_PROXY`, or a
  client's `proxy=`/`proxies=` setting) — the guard decides on the
  destination, so a request for a local address can still be sent through
  a public proxy.
- Raw sockets (`socket.socket()`) — wrapping these would break
  loopback connection paths underneath the wrapped libraries.
- Subprocess shells — `subprocess.run(["curl", ...])` and
  `os.system("curl ...")` go straight to the OS network stack.
- Other processes — the guard lives inside the Python processes that
  install it: the portal, any process that loads agents through the agent
  loader, Model Forge's worker, and `./arailctl doctor`. Setup's and
  upgrade's model downloads, Docker containers such as Open Notebook's,
  and opencode run outside it.

These are documented gaps for the v1 guard. The threat model is
well-meaning agent code that uses standard libraries; it is not
an adversary on the host. For host-level enforcement, run a
firewall (`pf` on macOS, `iptables`/`ufw` on Linux).
```

(Each claim was checked against the tree. The entry points that call `install_guard` are `portal/app.py:903`, `agents/loader.py:315`, `nucleus/worker.py:39` and `doctor.py:84`. setup and upgrade download through separate `python3 -c "...snapshot_download..."` processes (`scripts/setup.sh:1939`, `scripts/upgrade.sh:177`). Open Notebook runs through `docker compose` (`/api/open-notebook/start`). `DefaultAioHttpClient` exists in anthropic 2.3.0 and openai 3.42.1. No `urllib3`/`httpcore` import exists in `src/arail/`. The old sentence also named "the knowledge-canvas clients". That claim is dropped, because no single sentence about those clients is true. *(Erratum, review 2026-11-01, AF-3: the reason given here was "the Knowledge Canvas backend is a separate container outside any in-process guard". That is half true. The portal also mounts the backend in-process (`portal/app.py:760-766`), it imports in a stock ci and lock venv (run), and there its httpx calls are guarded where httpx is installed; the compose copy is started by nothing in the lab; `core/knowledge-canvas/client.py` runs wherever it is imported.)* *(Erratum, review 2026-11-01, AF-2 items 1 to 3: the block above now carries the wording the builder verified and wrote. The design said "the lab says so instead of running unguarded" (false: the portal and loader keep running with the library unguarded), "the portal logs" (it is an activity-log event) and "the agent loader" as a process (it is not one; `load_all` has one caller, the portal's startup). The wording still needs REVIEW.md B1, W1, W2 and W3 before it is complete.)*)

**`docs/PRIVACY.md` line 301** (the audit grep) becomes:

```bash
# Find where the Python code uses the HTTP clients and raw sockets this doc
# names (httpx also matches httpx2, httpcore also matches httpcore2)
grep -rnE "httpx|httpcore|urllib3|requests\.|urllib\.request|aiohttp|socket\.socket\(" src/arail/
```

*(Erratum, review 2026-11-01, AF-2 item 4: the comment said "Find every network call in the Python code". False for this pattern and the old one: `socket.create_connection` (`portal/services/opencode.py:268`) and `asyncio.open_connection` (`portal/app.py:2512`) are network calls it does not match, and SDK calls name no httpx-family library at the call site.)*

**`src/arail/portal/templates/_airgap_modal.html` lines 137-146** become:

```html
      <p class="mp-note airgap-toggle-note">
        The Python-level guard catches <code>requests</code>,
        <code>urllib.request</code>, <code>httpx</code> and its successor
        <code>httpx2</code> (so also the Hugging Face downloader and the
        Anthropic/OpenAI SDKs built on them). Other clients
        (<code>aiohttp</code>, <code>urllib3</code> or <code>httpcore</code>
        used directly, raw sockets, subprocess <code>curl</code>) are not
        wrapped, and a proxy you configure is not checked — see
        <a href="/docs/PRIVACY.md" class="airgap-privacy-link">docs/PRIVACY.md</a>
        for the full list. The threat model is well-meaning agent code,
        not an adversary on this host.
      </p>
```

**`src/arail/airgap.py` lines 7-16** (the docstring) become:

```
Threat model: well-meaning agent code that uses ``requests``,
``urllib.request`` or an httpx-family client (``httpx``, ``httpx2``). Not
an adversary on the host — for that, run a host firewall (pf on macOS,
iptables/ufw on Linux).

The guard itself lives in ``arail.egress``. Known gaps, documented in
PRIVACY.md and listed by the Network Policy modal (``KNOWN_GAPS`` below):
- ``aiohttp`` — not in tree.
- ``urllib3`` or ``httpcore``/``httpcore2`` used directly — the guard sits
  one layer up, in requests and the httpx family.
- A configured proxy — the guard decides on the destination, not the proxy.
- Raw sockets — wrapping would break loopback paths underneath wrapped libs.
- Subprocess shells — ``subprocess.run(["curl", ...])``, ``os.system("curl")``.
```

`KNOWN_GAPS` in `src/arail/airgap.py` (the first three strings are byte-identical to today's list in `portal/app.py:11974`):

```python
# What the Network Policy modal lists under "Known gaps"; /api/airgap/status appends any
# library the guard could not patch at runtime. Lives here (not in portal/app.py) so tests can
# read it without booting the portal; keep docs/PRIVACY.md in step (tests/test_egress_docs.py).
KNOWN_GAPS: tuple[str, ...] = (
    "raw socket connections (BUDDY_EGRESS_PROBE is the only audited use)",
    "subprocess-spawned tools (opencode CLI is config-gated to the local "
    "provider when airgapped; curl/wget: none in tree today)",
    "aiohttp (not in tree)",
    "urllib3, httpcore or httpcore2 called directly (the guard sits one layer "
    "up, in requests and httpx/httpx2; none in tree today)",
    "the hop to a configured proxy (HTTP_PROXY/HTTPS_PROXY or a client's "
    "proxy setting): the guard checks the destination, not the proxy",
)
```

**`src/arail/router/core.py` lines 14-17** (the comment) become:

```
# Backends whose inference calls leave this machine. Constructing one of
# these while airgapped is refused up front — the egress guard (requests,
# urllib.request, httpx, httpx2) would block the eventual API call anyway,
# but failing here gives an immediate, non-network, user-readable error.
```

**`src/arail/egress.py`:** the module docstring's first paragraph becomes:

```
Installs a guarded requests HTTPAdapter, a guarded urllib opener, and a
check on the transports of every installed httpx-family library (``httpx``
and its successor ``httpx2``, named once in ``arail.httpx_family``), each
of which consults the airgap policy before a request leaves.

The httpx-family check is patched onto ``HTTPTransport.handle_request`` and
``AsyncHTTPTransport.handle_async_request`` at class level, so it also
covers clients built before install_guard() ran and the clients SDKs build
for themselves (huggingface_hub, anthropic, openai). In-process transports
(ASGITransport, WSGITransport, MockTransport, starlette's TestClient) are
other classes and are left alone. Not covered (docs/PRIVACY.md): aiohttp;
urllib3 or httpcore/httpcore2 used directly; raw sockets; subprocesses; the
hop to a configured proxy (the decision is made on the destination).

If an httpx-family library is installed but cannot be patched,
install_guard() patches everything else and raises EgressGuardIncomplete:
a library the guard cannot see must never be a silent hole.
```

The rest of the module docstring (usage, `allow_egress`, the probe) stays. The `install_guard` docstring describes the 3 steps, the raise-after-patching rule, `_INSTALLED` staying False, and the marker and alias idempotency. `_install_httpx_family_guard`'s docstring drops "missing httpx is a silent no-op" and says absent means `"absent"` (nothing can use it), while unpatchable means a raise.

## Tech debt

**Added**
- A 5-leg workflow whose declarations must be updated by hand when the ecosystem moves (for example, if hf ever leaves httpx2). This is intended friction: a red run is the signal. The cost is a few lines when it happens.
- Four small public names (`coverage()`, `uncovered()`, `EgressGuardIncomplete`, `GUARDED_CLIENTS`) and one new module (`arail.httpx_family`).
- Tripwires that turn red on upstream changes (a new `httpx3`, a new transport class). They are intended, and each message says what to do.
- A deliberate, logged edit to `tests/test_qa_airgap_happy_setup.py` (its gap lists).

**Repaid**
- TD50 (closed).
- The runtime half of TD51.
- B5's two silent skips.
- B6: no CI ran any egress test.
- The latent saved-adapter bug on a retried install.
- The startswith accident in caller attribution.
- The stale `airgap.py` "httpx — not wrapped".
- PRIVACY's over-claim about the "knowledge-canvas clients".
- B4 and the proxy hop, from undocumented to documented and pinned.
- `KNOWN_GAPS` moved out of a 12k-line module.

**Net:** negative. New rows file the remainder:

| # | Debt | Trigger that pays it down |
|---|---|---|
| TD52 | **Test modules import `httpx` by name, so they break where only `httpx2` is installed (TD51's test half).** Measured 2026-11-01 in a venv built as CI builds its own, on untouched main: `tests/test_host_edge.py`, `tests/test_host_edge_brain.py` and `tests/portal/test_opencode_routes.py` fail to collect (module-level `import httpx`). `tests/buddy_conformance.py:346` (`socket_edge_client`) raises `ModuleNotFoundError` in 132 of `tests/test_host_edge_conformance.py`'s cases. `tests/test_portal_login.py:235` filters the test client's request log by the logger name `httpx`, but httpx2 logs as `httpx2`, so `test_token_never_appears_in_logs_or_activity` fails on the client's own log line (the portal's redaction is not at fault). `tests/test_host_edge_brain.py` also needs `openai`, and must build its client on the library `openai` itself uses, not on `httpx_family.preferred()`. No workflow runs these files. Fix: `httpx = arail.httpx_family.preferred()` for the ASGI-transport users; filter the log on `httpx_family.FAMILY`; build the brain test's client from `openai`'s own library. | Any of these files added to a workflow; the next change to `tests/buddy_conformance.py` or the host edge tests; or TD49's decision. |
| TD53 | **Direct `urllib3`, `httpcore` or `httpcore2` use is outside the egress guard** (B4 of TD50's baseline). The guard sits in `requests` and the httpx family, one layer up. Verified 2026-11-01: with `LAB_MODE=airgapped`, `urllib3.PoolManager().request(...)` and `httpcore2.request(...)` to a reserved name end in `NameResolutionError` and `ConnectError`, never `EgressBlocked`. Nothing in `src/arail/` calls them directly; lancedb's `lance-namespace-urllib3-client` is installed and is a direct `urllib3` client (whether a lab path reaches it was not checked). Documented in `docs/PRIVACY.md`, the modal and `airgap.KNOWN_GAPS`; pinned by documented-gap tripwires. Fix: patch the pool layer (`urllib3.connectionpool.HTTPConnectionPool.urlopen`; `httpcore(2).ConnectionPool.handle_request` and the async pool) with the same decision. | Lab code, or a dependency running in a lab process, calls `urllib3` or `httpcore` directly for a non-local host; or a release note claims that every HTTP client is guarded. |
| TD54 | **The egress guard decides on the destination, not on a configured proxy.** Verified 2026-11-01 (airgapped, guard installed): a request to `127.0.0.1` through a proxy at a public name (`proxy=`/`proxies=` or `HTTP_PROXY`) reaches the network for the proxy with `requests` (`ProxyError`) and with `httpx2` (`ConnectError`), never `EgressBlocked`. So a request body meant for a local service can be sent to a public proxy. The reverse (a loopback proxy and a public destination) is refused. Documented and pinned (BY-4). Fix: in `GuardedHTTPAdapter.send`, check the proxy that `requests.utils.select_proxy` picks; in the httpx-family wrapper, check the transport's proxy URL (needs a supported way to read it). | A user report with `HTTP(S)_PROXY` set, or the next change to the decision functions in `egress.py`. |
| TD55 | **Hybrid mode's audit claim is not true.** `docs/PRIVACY.md` ("What hybrid mode sends") says the egress log records every outbound call with `reason: "hybrid"`. The portal's hybrid definition (`/api/airgap/status`) says it "still records all outbound calls". No code writes a `hybrid` line: in hybrid, requests and the httpx family pass calls through unrecorded. Only `allow_egress` and bootstrap scopes write `allow:` lines, and urllib's `allow_egress` path writes none. This was read from the code on 2026-11-01 (every `record_*` call site), not run. Fix: either record hybrid pass-throughs (rate-capped) or say what is recorded. | The next change to the modal, the definitions or the egress log; before any release that repeats the claim. |

TD50's row becomes closed. TD51 is re-scoped: its runtime half closes, and its test half moves to TD52. The exact replacement text is in "Atomic commit plan", commit 8. TD49 is unchanged. The new legs make a guard regression from drift visible, but they do not decide floating versus lock.

## Recommended implementation order

1. The resolver module.
2. The family guard and in-process tests (the TD50 fix).
3. Fresh-interpreter probes, huggingface_hub and the SDK test file.
4. Tripwires and documented-gap pins.
5. The runtime TD51 consumers (seed, doctor).
6. Docs, `KNOWN_GAPS` and the status endpoint.
7. The CI workflow and its checker.
8. After #211 merges: the BACKLOG rows.

The order lets every commit be green alone. The fix lands with its regression tests (2) before anything that only describes it (6).

## Atomic commit plan

Before each commit, run the targeted files it touches (plus `tests/test_egress_*.py`, `tests/test_qa_airgap_bypass_attempts.py` and `tests/test_queuellm_rename_frozen_surface.py`) in the **ci**, **lock** and **both** venvs. From commit 2 on, also run EG-0 and EG-0b in the **neither** venv: EG-0 must fail and EG-0b must pass with `ARAIL_TEST_EXPECT_HTTPX_FAMILY=none`. Run one pytest at a time, with no full-suite run beside another.

1. **`feat(arail): arail.httpx_family, the one place that names httpx and httpx2`**: `src/arail/httpx_family.py`; tests HF-1 to HF-6 in `tests/test_httpx_family.py`. Pure addition.
2. **`fix(egress): guard every installed httpx-family library, not only httpx (TD50)`**: `egress.py`:
   - the family patch keyed by (class, attribute);
   - `coverage()`, `uncovered()`, `GUARDED_CLIENTS` and `EgressGuardIncomplete`;
   - the RLock and the idempotent saving of the `requests` adapter;
   - exact or dotted-prefix `_SKIP_MODULES` with `httpx2` and `httpcore2`;
   - pass-through `*args, **kwargs` in the wrappers;
   - the docstrings.

   Tests:
   - `tests/egress_family_support.py`;
   - `tests/test_egress_httpx.py` rewritten (EG-0 to EG-22);
   - `tests/test_qa_airgap_bypass_attempts.py` BY-1 with the skip removed, plus its docstrings.
3. **`test(egress): the brief's probe, huggingface_hub and the SDKs in a fresh interpreter`**: `tests/test_egress_httpx_fresh.py` (FR-1 to FR-5) and `tests/test_egress_sdk_clients.py` (SDK-1, skip unless installed or declared). This commit makes no source change.
4. **`test(egress): tripwires for unknown httpx-family modules and transports, and pins for the documented lower-layer and proxy gaps`**: EG-23 and EG-24 in `tests/test_egress_httpx.py`; BY-2 to BY-4 in `tests/test_qa_airgap_bypass_attempts.py`.
5. **`fix(arail): open_notebook_seed and the doctor resolve httpx through arail.httpx_family (TD51, runtime)`**:
   - `open_notebook_seed.py:19`;
   - `doctor.py`: the components list and the coverage line;
   - ON-1, ON-2 and DR-1 to DR-3 in `tests/test_httpx_family.py`.
6. **`docs(egress): say what the guard actually wraps, and list the gaps it does not`**:
   - `README.md`, `docs/PRIVACY.md`, the modal paragraph, the `airgap.py` docstring and `KNOWN_GAPS`, and the `router/core.py` comment;
   - `portal/app.py`: `/api/airgap/status` reads `airgap.KNOWN_GAPS` and appends `uncovered()`;
   - `tests/test_egress_docs.py` (DOC-1 to DOC-5);
   - the deliberate edit to `tests/test_qa_airgap_happy_setup.py`, logged in BUILD_LOG.
7. **`ci(egress): run the guard's tests in every httpx-family state, daily`**: `.github/workflows/egress-guard.yml`, `tests/egress_ci_check.py`, `tests/test_egress_ci_check.py` (CI-1 to CI-4). Validate the YAML locally with `python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))"`, and with `actionlint` if it is available. The real proof is the PR's CI run.
8. **(After #211 merges.) `sprint(egress-httpx2): BACKLOG, close TD50, re-scope TD51, file TD52 to TD55`**: `sprints/BACKLOG.md` only, plus the ledger.
   - TD50's row is replaced with: "**Closed 2026-11-NN.** The egress guard patched `httpx` only, so `httpx2` (huggingface-hub 2.x, anthropic 2.x, openai 3.x, starlette 1.x) ran unguarded on a fresh install and the tests that pinned the guard skipped themselves. Fix: the guard patches every installed library in `arail.httpx_family.FAMILY` (`httpx2`, `httpx`) at class level, keyed by class so an alias is wrapped once. A library that is installed but cannot be patched raises `EgressGuardIncomplete` after everything else is patched (the doctor fails, the modal lists it). The tests run per installed library with no skip, plus a sentinel that fails when neither is installed. `.github/workflows/egress-guard.yml` proves it in five states, daily. Full account: `sprints/2026-11-01-arail-egress-httpx2/`. | Closed by arail #NNN; proved by the five legs on that PR."
   - TD51's row is replaced with: "**Runtime half closed 2026-11-NN; the test half is TD52.** `src/arail/open_notebook_seed.py` and `src/arail/doctor.py` resolve the library through `arail.httpx_family` (httpx2 preferred, as starlette does). | Runtime closed by arail #NNN; tests: TD52."
   - Append TD52 to TD55 from "Tech debt", renumbered if needed.

## Evidence the sprint must produce

Each item follows the #211 format: what it means, the bar and why, what out of range means, and where a failure lives.

**E1. The brief's probe in a fresh interpreter, before and after, in the ci (httpx2 2.16.0), lock (httpx 0.28.1) and both venvs, plus leg 2 (Python 3.10, httpx2 2.14.0) from CI.** The output is a table of library × variant (sync-after, which is the literal probe; sync-before; async-after; async-before), giving the outcome and the seconds.
- *Means:* `EgressBlocked` is the guard refusing before any socket I/O. `ConnectError` is the request reaching the network layer.
- *Bar and why:* after the change, `EgressBlocked` in every cell. Before the change, `ConnectError` in every httpx2 cell, reproducing B1, which shows the probe can fail. The airgap promise holds only if every library a client can be built on is refused.
- *Out of range:* any other outcome after the change means the guard missed that client shape. `NO EXCEPTION` on a `.invalid` host means the resolver answers reserved names (a DNS wildcard); look at the machine before the guard.
- *Where:* `egress._install_httpx_family_guard`; `tests/test_egress_httpx_fresh.py::FR-1`.

**E2. Install first, then import (FR-2), the same venvs.**
- *Means:* the guard patched a library nothing had imported yet (eager import).
- *Bar and why:* `EgressBlocked`, sync and async, in every leg. Portal start installs the guard before most imports.
- *Out of range:* anything else means the eager import is broken; look at `httpx_family.is_installed` and the import step.
- *Where:* FR-2.

**E3. huggingface_hub, ci (2.4.0) and lock (1.25.1): `model_info`, `hf_hub_download`, `snapshot_download`, and a shared session built before install.** Report the final exception, the number of refusals per call, and the seconds.
- *Means:* one audit line is one attempt the guard refused. B2's baseline was 5 retries with backoff, then `LocalEntryNotFoundError`.
- *Bar and why:* final `EgressBlocked`, exactly 1 refusal per call, under 1 s. A refusal must not spend hf's retry budget on the network, and must not flood the audit log. The architect measured 1 refusal and at most 0.02 s on both versions.
- *Out of range:* more than 1 refusal means hf started retrying a `RuntimeError`. A final `LocalEntryNotFoundError` with `EgressBlocked` in its chain means hf started wrapping it; decide on purpose whether to relax FR-5. `ConnectError` means it is unguarded.
- *Where:* FR-5; `huggingface_hub.utils._http`.

**E4. SDKs (leg 1, and QA once): anthropic 2.3.0 and openai 3.42.1 default clients, built before and after install, with `max_retries=0` and with defaults.** Report the exception chain, the refusals and the seconds.
- *Means:* anthropic wraps the refusal in `APIConnectionError` and retries it (the architect measured 3 refusals in about 1.4 s with defaults). openai lets `EgressBlocked` through (1 refusal).
- *Bar and why:* `EgressBlocked` in the chain, no network-error type in the chain, and exactly 1 refusal at `max_retries=0`. That is proof no attempt left; the retry count with defaults is the SDK's own business.
- *Out of range:* `ConnectError` or `gaierror` in the chain means the SDK's default client left the guarded transports (for example, an aiohttp default). That calls for an architect decision, not a test edit.
- *Where:* SDK-1; the SDK's `_base_client`.

**E5. Client shapes, QA once with `h2`, `socksio` and `wsproto` installed:**
- refused: `proxy` (http and socks5) at loopback, `mounts`, env proxy, `transport=`, a subclass calling `super()`, `http2=True`, `stream()`, the second hop of a redirect, `websocket()`;
- pass: in-process transports and `TestClient`.
- *Means:* each shape reaches `handle_request` on the patched class.
- *Bar and why:* all refused, all in-process pass. The architect measured exactly this on httpx 0.28.1 and httpx2 2.16.0.
- *Out of range:* a refused shape that passes is a bypass and needs a test and a fix. A blocked in-process transport would break the portal tests.
- *Where:* EG-10 to EG-13; `probe_paths_proto.py` in session scratch.

**E6. The targeted egress list, per venv (ci, lock, both, and leg 2): passed, failed and skipped, per file.**
- *Means:* the counts a leg runs.
- *Bar and why:* 0 failed and **0 skipped** in every leg, and every file contributes at least 1 test. A skip is how TD50 stayed hidden (B5: 1 + 1 skips in CI-style).
- *Out of range:* any skip: name the test and the reason. A file with 0 tests means a path typo or a collection change.
- *Where:* the junit check output; the file named.

**E7. Negative control, the neither venv.**
- *Means:* the sentinel's failure is what proves the httpx-family claim was actually exercised in the other legs.
- *Bar and why:* EG-0 fails with `UNPROVEN`, EG-0b passes, pytest exits 1. Before the change, the same venv ran the egress files green: **79 passed, 2 skipped, exit 0** (architect, 2026-11-01). That is the vacuous pass removed.
- *Out of range:* exit 4 or 5 means a collection problem (a module-level import of an HTTP library or hf crept into `tests/test_egress_httpx.py`). EG-0 passing means the sentinel uses the guard's own lookup, or the venv is not "neither". *(Erratum, review 2026-11-01, AF-2 item 7: this said "exit 2 or 5". With explicit node ids, which is what leg 5 runs, a module that fails to import makes pytest exit 4; with a file path it exits 2 (run). The checker rejects every status but 1 either way.)*
- *Where:* `tests/test_egress_httpx.py`; `tests/egress_ci_check.py`.

**E8. Full suite, before and after, in the ci and lock venvs. One pytest per file, one run at a time, with nothing else holding port 8089.**
- *Means:* the set of failing ids on each side.
- *Bar and why:* the same failing ids on both sides. The session's background baseline on untouched main is 78 failed plus 8 errors over 31 files in lock, and 156 files with a failure in CI-style, mostly TD48 and TD52. This PR changes the guard in every process that boots the portal, so in CI-style venvs `httpx2` is now patched in those tests (F28).
- *Out of range:* an id that fails only after the change is a regression until a serial re-run on both trees says otherwise. The likely shape is a test that boots the portal and then calls a public host through httpx2. Fix the test's assumption; never the guard.
- *Where:* the id's file; conftest `_reset_egress_guard`.

**E9. CI on the PR: all 5 legs.**
- *Means:* each leg's "What this leg installed" block, plus its pytest summary.
- *Bar and why:*
  - legs 1 to 4 green, with their blocks matching their declarations;
  - leg 5 green *because* its sentinel failed (its log shows the `UNPROVEN` failure);
  - leg 1's SDK step green with 0 skips.
- *Out of range:* red on install means a PyPI transient; re-run once. Red on the declaration means the state changed; read the installed block. Red on zero-skip names the test. Leg 2 resolving httpx instead of httpx2 is a decision to record, not a failure to hide.
- *Where:* `.github/workflows/egress-guard.yml`; the named step.

**E10. Canaries: each new check is shown to fail.** In a scratch copy, never committed:
- (a) set `FAMILY = ("httpx",)`: in the ci venv FR-1 and EG-1 to EG-4 fail *(erratum, review 2026-11-01, AF-2 item 5: this also listed DOC-1, which cannot fail here because `GUARDED_CLIENTS` derives from `FAMILY`; DOC-1 fails when a member is added to `FAMILY` without docs, shown in step 6)*;
- (b) drop `"httpx2"` from `_SKIP_MODULES`: EG-21, EG-22 and EG-1's caller assertion fail;
- (c) make reset skip one restore: EG-16 and EG-17 fail;
- (d) put an empty package `httpx3/` on `sys.path`: EG-23 fails with its message;
- (e) make `egress_ci_check.py no-skips` ignore skips: CI-2 fails;
- (f) make EG-0 always pass: the negative-control check fails.
- *Means:* a check that cannot fail proves nothing (#211's rule).
- *Bar and why:* each mutation turns at least one named test red.
- *Out of range:* a mutation with nothing red means a vacuous test; fix the test before review.
- *Where:* the test named for the mutation.

**E11. Import cost added to `install_guard`.**
- *Means:* the time a process that already imported `arail.egress` pays to import the family. The architect measured httpx2 about 6.5 ms and httpx about 8.3 ms (cold, alone: about 32 ms each).
- *Bar and why:* under 50 ms each. Portal start takes seconds; the doctor and the worker are CLI tools.
- *Out of range:* revisit decision 3 (lazy import) for the CLI paths.
- *Where:* `-X importtime` on `arail.egress` followed by the library.

**E12. Doctor output in the ci, lock and both venvs, and with a simulated incomplete guard.**
- *Means:* what an operator sees.
- *Bar and why:*
  - every family member is listed with its version or "not installed";
  - the egress line names the guarded clients;
  - in the simulated case, the required check fails and names the library.
- *Out of range:* "httpx: not installed" with no httpx2 line means `doctor.py` still has the old list.
- *Where:* DR-1 to DR-3; `doctor.check_components` and `check_environment`.

**E13. Order independence.**
- *Means:* patch state does not leak between files.
- *Bar and why:* each egress file passes alone, and the whole list passes in forward and in reverse file order, with identical counts. The suite shares one process and module-level patch state.
- *Out of range:* a difference means a test left state behind; look at the file's autouse reset and at `_reset_for_tests`.
- *Where:* the file whose result moved.

## Open questions for the orchestrator

1. **TD55 (hybrid audit claim):** file only (the default), or fold the doc and definition fix into this PR? It is a docs/portal-string change of about two sentences, but it is a third debt, and the brief asked for a focused PR.
2. **Required checks:** should the five "Egress guard: …" legs become required on main once they have been green twice? That is the operator's GitHub setting. Once they are required, the leg names are a contract.
3. **Cadence:** daily (chosen: the repo is public so it costs nothing, and it matches Blueprint Smoke) or weekly (less notification noise)? Scheduled failures notify whoever last edited the `cron` line.
4. **TD numbers:** TD52 to TD55 assume #211 merges as it is and nothing else files TD52 first. Confirm at commit 8.
5. **Fail-closed worker:** with an incomplete guard, `python -m arail.nucleus.worker` exits non-zero even in hybrid mode (`install_guard` is not wrapped there). The default is to accept that, since the toggle can flip to airgapped at runtime. Confirm.
6. **Behaviour change for the PR description:** fresh (httpx2) installs now refuse httpx2-based egress in airgapped mode (Hugging Face downloads, SDK calls in agents), as lock installs already did for httpx. Whisper STT checks airgapped mode before downloading, and the router refuses cloud backends first, so the change in the lab's own surfaces should be small. The operator may want to call it out.

## Errata and additions from the review (2026-11-01)

The architect's review ([REVIEW.md](./REVIEW.md)) corrected this file in place where a statement was false; each correction is marked *Erratum, review 2026-11-01* where it stands. In one list:

| Where | Was | Now | Source |
|---|---|---|---|
| Decision 1, "Why" | Every caller turns the exception into a visible signal | Amended: the portal and loader keep serving with the library open and the UI contradicts it; fix and TD56 | REVIEW.md B1 |
| A9 | Loudness depends on the callers | Noted: it is weaker than designed | B1, N2 |
| Test strategy, "No real host" | `gethostbyname` answers TEST-NET-3 `203.0.113.7` | `151.101.64.81` (`PUBLIC_TEST_IP`); documentation ranges are private to `ipaddress` | AF-1 |
| Documentation, PRIVACY Known gaps | "instead of running unguarded", "the portal logs", "the agent loader" as a process | The builder's verified wording | AF-2 items 1 to 3 |
| Documentation, the Knowledge Canvas reason | A separate container outside any guard | Also mounted in-process by the portal; no single true sentence, so the claim stays dropped | AF-3 |
| Documentation, PRIVACY audit grep | "Find every network call" | "Find where the Python code uses the HTTP clients and raw sockets this doc names" | AF-2 item 4 |
| E7 | Exit 2 or 5 on a collection problem | Exit 4 with explicit node ids (2 with a file path) | AF-2 item 7 |
| E10(a) | DOC-1 fails for `FAMILY = ("httpx",)` | It cannot; DOC-1 fails for a member added without docs | AF-2 item 5 |

New debt found in review. Numbers are provisional and are confirmed at commit 8 with TD52 to TD55.

| # | Debt | Trigger that pays it down |
|---|---|---|
| TD52 (addition) | Three `import httpx` sites outside `src/arail` and `tests`: `core/knowledge-canvas/backend/app/services/llm_router.py:32` (inside `_ollama_fallback`; the portal mounts the canvas backend in-process at `portal/app.py:760-766`, and on an httpx2-only venv the fallback raises `ModuleNotFoundError: No module named 'httpx'`, run), `core/knowledge-canvas/client.py:25` (module level; not importable under its documented path `core.knowledge_canvas`, the directory is hyphenated), `scripts/deep_honest_win.py:717` (an in-process ASGI harness). None is an egress hole; the canvas's own `requirements.txt` declares `httpx>=0.27`. Fix: `try: import httpx2 as httpx` / `except ImportError: import httpx` in the canvas (no `arail` import), `arail.httpx_family.preferred()` in the script. | The first canvas NLQ call on an httpx2-only install; the next change to the canvas backend or to the witness script. |
| TD56 | **The egress guard does not fail closed when it is incomplete.** With a family member installed but unpatchable, the portal and the agent loader keep serving in airgapped mode with that library unguarded (REVIEW.md B1 makes the UI say so; it does not close the hole). Fix: refuse to start in airgapped mode, and refuse the toggle to airgapped, while `egress.uncovered()` names a library whose import succeeded; a readable message (no traceback) that names the library and the fix. | Before any release whose pins allow an httpx or httpx2 major that renames a transport; or the first daily leg that goes red on `EgressGuardIncomplete`. |
| TD57 | **A family member installed after `install_guard()` is unguarded until restart.** `coverage()` keeps saying `absent` and a second `install_guard()` is a no-op (run: lock venv, httpx2 made importable after the guard, `ConnectError`). Reachable through `/api/plugins/install`, which pip-installs into the running portal. Fix: a `sys.meta_path` hook, registered for the members that were absent at install time, that patches the module in its loader's `exec_module` before any importer sees it; a fresh-interpreter test. | The next change to the plugin installer or the tier switch; a plugin whose requirements pull huggingface-hub 2.x, anthropic 2.x or openai 3.x into a lock-based lab. |
| TD58 | **Lab-spawned Python subprocesses run without the guard.** `arail.skills.goal_parser._subprocess_runner` (runs the ModelRouter) and `arail.router.airllm_worker` (`airllm_worker.py:74` falls back to the hub repo id when the local model directory is missing) never call `install_guard()`. Read, not run. Fix: install the guard first thing in each, or document each as outside it. | The next change to either subprocess; any report of a download in airgapped mode. |
| TD59 | **`airgap.is_local_host` sets the process-wide default socket timeout around each DNS lookup** (`airgap.py:136-144`). The timeout does not bound `gethostbyname`, and another thread that opens a socket in that window inherits 1.5 s. Pre-existing; since TD50's fix every httpx2 request to a hostname takes this path. Fix: drop the `setdefaulttimeout` dance, or resolve with a bounded `getaddrinfo` in a worker. | The next change to `airgap.is_local_host`; any flaky socket timeout seen in the portal. |

## Appendix: what the architect verified (2026-11-01, this Mac)

These results come from a runtime-injected prototype (`<scratch>/arch/proto_guard.py`), not the implementation. They show the design works. The builder's code must pass the tests above on its own.

| # | Check | Result | Means |
|---|---|---|---|
| V1 | Brief probe plus `module-get`, prototype; ci, lock, both, Python 3.10 with httpx2 2.14.0, Python 3.11 with httpx2 2.14.0; also install-first order | `EgressBlocked` in every cell, 0.00 to 0.02 s; today's code in ci: `ConnectError` in every cell | The family patch closes B1 in every state and order. |
| V2 | huggingface_hub 2.4.0 (ci, Python 3.10) and 1.25.1 (lock) | Final `EgressBlocked`, 1 refusal per call, at most 0.02 s; the shared session built before install is refused too | No retry backoff once refused (B2 closed). |
| V3 | anthropic 2.3.0, openai 3.42.1 (built before and after) | anthropic: `APIConnectionError ← EgressBlocked` (defaults: 3 refusals, about 1.4 s; `max_retries=0`: 1). openai: `EgressBlocked`, 1 refusal. Today: `APIConnectionError ← ConnectError ← gaierror` | Both default clients ride on `httpx2.HTTPTransport`; refused once patched. |
| V4 | Client shapes (with `h2`, `socksio`, `wsproto`) | All refused; loopback, Mock, WSGI and ASGI pass | Transport class patching is the right choke point. |
| V5 | `alias_httpx()` before install, family in both orders (ci, both) | 1 wrap, 1 audit line, reset restores; `alias_httpx()` *after* install in both: `RuntimeError` (httpx already imported) | The marker plus class-keyed state handle double wrap; A3. |
| V6 | `AsyncHTTPTransport` deleted | `EgressGuardIncomplete`; `requests` and the httpx2 sync path still guarded; a retried install plus reset leaves `GuardedHTTPAdapter` in `requests` (today's capture) | Raise after patching the rest; F7 is real. |
| V7 | Caller attribution | Today `__main__.user_code`; exact match without an `httpx2` entry: `httpx2._client.Client._send_single_request` | httpx2 is skipped by accident today (F8). |
| V8 | Import cost | httpx2 about 6.5 ms, httpx about 8.3 ms after `arail.egress`; cold about 32 ms each | Eager import is fine (decision 3). |
| V9 | Neither venv, today's egress files | 79 passed, 2 skipped, exit 0 | The vacuous pass leg 5 removes. |
| V10 | `uv pip compile` | Python 3.10: httpx2 2.14.0 plus hf 2.4.0. Python 3.11 and 3.12: httpx2 2.16.0. `maximus` on 3.11: httpx 0.28.1 *and* httpx2 2.16.0 | Leg 2's state; "both" is a real maximus state. |
| V11 | CI-style full suite, untouched main (session background run) | TD51 test files: 3 collection errors, 132 failures via `buddy_conformance.py:346`, 1 failure from `test_portal_login.py:235` | TD52's inventory. |
| V12 | Public proxy, loopback destination | `requests`: `ProxyError`; httpx2 (prototype): `ConnectError` | TD54 is real. |
| V13 | starlette `TestClient` with the guard installed, airgapped | 200 on 1.10.0 (httpx2) and 1.3.1 (httpx) | In-process transports are untouched. |
