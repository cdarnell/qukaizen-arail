# Test report: the egress guard covers the httpx family (TD50, runtime half of TD51)

**Date:** 2026-11-04
**Build:** [BUILD_LOG.md](./BUILD_LOG.md) at `46099a64` (pass 3 results; code head `55107aeb`)
**Reviewer:** qa, Claude Opus 5.5. Every result below marked *run* was executed in this session. Scratch probes live under `$S/qa/` (`$S` = the session scratchpad `<session scratchpad>`); they are not committed.
**Verdict:** WEAK_PASS

## Verdict and why

WEAK_PASS. I re-measured everything the sprint claims for the guard itself, and it holds. Every E5 client shape is refused in airgapped mode and every in-process transport still passes. The newer SDK releases (anthropic 2.5.0, openai 3.46.0) are refused with no network error in the chain. A clean `pip install -e .` lab installs the guard in about 14 ms, and the brief's probe ends in `EgressBlocked`. The 13 CI files pass forward and reversed in four library states, all five CI legs are green from the workflow's own text, the neighbour files fail exactly as on `origin/main`, and the frozen-surface failure text is unchanged. No test fails, including the 10 to 15 boundary tests I added (they also pass in every state). Three WEAK findings stop this from being a PASS:

- **QA-W2** breaks the PR's own failure-state contract. If an httpx-family module's attribute lookup raises something other than `AttributeError`, the guard aborts. The healthy second library is then left unpatched, `coverage()` stays empty, and the portal still says "Cloud-provider APIs are blocked." A four-line fix is validated below.
- **QA-W1:** README and PRIVACY say `urllib.request` is guarded, but `urlopen(url, context=...)`, `http.client`, and any later `install_opener()` all get past the guard. None of these is documented. I have now pinned them with tests.
- **QA-W3:** an `allow_egress` scope opened in hybrid keeps allowing requests after the lab flips to airgapped. This is pre-existing, and a three-line fix is validated.

None of the three is a BLOCK, because no supported release and no in-tree code path reaches it today. Eight NITs and one pre-existing security item are listed for completeness.

## Findings

Severity: **BLOCK** must change before ship; **WEAK** change in a quick fix pass or file it as debt, as each item says; **NIT** optional. Counts: BLOCK 0, WEAK 3, NIT 8, plus one pre-existing item outside the diff (not counted).

### QA-W2 (WEAK, fix before merge). A family module whose attribute lookup raises aborts the family step: the next library stays unpatched and the UI says "blocked"

- **Where:** `src/arail/egress.py:515`. `result[name] = _patch_family_member(name, module)` sits outside the `try` that wraps the import (510-514). `:464`: `getattr(module, cls_name, None)` swallows `AttributeError` only. `:662-663`: `_COVERAGE = found` is never reached. Downstream, `portal/app.py` builds the definition from `uncovered()`, and `nucleus/worker.py:46-52` catches `EgressGuardIncomplete` only.
- **Evidence (run):** `$S/qa/probes/state_status.py lazy-attr` and `state_cli.py lazy-attr`, both venv (httpx 0.28.1 and httpx2 2.16.0), airgapped. `httpx2` is replaced by a module whose `__getattr__` raises `ImportError` for the two transports. This is the PEP 562 lazy-module shape: httpx2 2.14.0 and 2.16.0 already have a module `__getattr__` that lazy-imports `main` from `._main`. Results:
  - `install_guard()` raises a raw `ImportError`, `coverage() == {}`, and `_INSTALLED` stays False.
  - **httpx (healthy, second in `FAMILY`) is never patched.** Its sync and async requests to a `.invalid` host end in `ConnectError` (they reached the network layer).
  - `/api/airgap/status`: `guard_installed: false`, definition "…Cloud-provider APIs are blocked. …" (unqualified), and `known_gaps` names no library.
  - `nav.js` under node: the pill is `warn` with the tooltip "could not patch every HTTP library — see below". The list opens, but nothing in it names a library.
  - Doctor: `egress guard : ? (ImportError: …)`, exit 3. Model Forge worker: a raw `ImportError` traceback, exit 1.
  - Contrast (run): when the *import* raises (EG-A3's mode), every surface is consistent and httpx stays guarded.
- **Why WEAK, not BLOCK:** no supported release is in this state, because the transports are eager attributes today. The daily legs turn red the day one is: EG-24 reads `mod.HTTPTransport`. The pill does turn amber. But this recreates REVIEW B1's contradiction, for a library the guard could have patched, and it breaks the interface promise "everything that CAN be patched is patched either way".
- **Fix (validated on a scratch copy, `$S/qa/fix-lazy-*`):**
  ```python
          try:
              result[name] = _patch_family_member(name, module)
          except Exception as exc:  # noqa: BLE001 — a lookup on the module raised (a lazy __getattr__)
              result[name] = f"{_UNGUARDED}patch raised {type(exc).__name__}: {exc}"
  ```
  With the fix, the same probe gives:
  - httpx sync and async are both `EgressBlocked`.
  - `coverage()["httpx2"]` is `"unguarded: patch raised ImportError: …"` and `EgressGuardIncomplete` is raised.
  - The activity warning appears, the definition is qualified ("EXCEPT requests made through httpx2"), `known_gaps` has the entry, and the `nav.js` list names it.
  - The worker prints its readable line, exit 1.
  - The 13 files stay green: ci 372, lock 372, both 421.
- **Proposed test** (for `tests/test_egress_httpx.py`, next to EG-A3). It fails on HEAD in both and ci and passes with the fix. It is not committed, because a red test would turn every CI leg red:
  ```python
  def test_a_member_whose_attribute_lookup_raises_is_loud_and_the_next_member_is_still_patched(monkeypatch):
      first = LITERAL_FAMILY[0]
      lazy = types.ModuleType(first)

      def __getattr__(name):
          if name in ("HTTPTransport", "AsyncHTTPTransport"):
              raise ImportError("simulated: a lazy attribute whose import fails")
          raise AttributeError(name)

      lazy.__getattr__ = __getattr__
      monkeypatch.setitem(sys.modules, first, lazy)
      with pytest.raises(egress.EgressGuardIncomplete) as excinfo:
          egress.install_guard()
      coverage = egress.coverage()
      assert coverage[first].startswith("unguarded: ") and "ImportError" in coverage[first], coverage
      assert first in excinfo.value.uncovered
      assert coverage["requests"] == "guarded" and coverage["urllib.request"] == "guarded"
      for other in INSTALLED:
          if other != first:
              assert coverage[other] == "guarded", coverage
              assert getattr(_owned(importlib.import_module(other), "HTTPTransport", "handle_request"), "_arail_guarded", False)
  ```

### QA-W1 (WEAK, docs). `urllib.request` is guarded only through the opener the guard installs; three everyday shapes bypass it, and none is documented

- **Where:**
  - Code: `src/arail/egress.py:653-658` installs one opener.
  - Claims: `README.md:302-303` ("`urllib.request` (the default opener)"); `docs/PRIVACY.md:43-44` ("blocks calls made through `requests`, `urllib.request`, …"); `docs/PRIVACY.md:96-99` ("the guard swaps the defaults (…, the opener `urlopen` uses)").
  - Gap lists that omit these shapes: `src/arail/airgap.py:43-52` (`KNOWN_GAPS`) and the modal paragraph (`_airgap_modal.html:138-148`).
- **Evidence (run):** `$S/qa/probes/stdlib_gaps.py`, ci (3.11.17) and py310 (3.10.22), airgapped, guard installed, `.invalid` host.

  | Shape | Outcome | Audit lines |
  |---|---|---|
  | `urlopen(url)`, `urlopen(Request)`, `urlretrieve` | `EgressBlocked` | +1 each |
  | `urlopen(https_url, context=ssl.create_default_context())`; the same for `http://` | `URLError` | 0 |
  | `urlopen(..., cafile=…)` | `URLError` | 0 |
  | `http.client.HTTPConnection` / `HTTPSConnection` `.request` | `gaierror` | 0 |
  | `xmlrpc.client.ServerProxy(...).ping()` | `gaierror` | 0 |
  | after another library's `install_opener(build_opener(...))` | `URLError` | 0 |
  | the guard's opener put back | `EgressBlocked` | +1 |

  CPython's `urlopen` builds a fresh opener with stock handlers whenever `context`, `cafile` or `capath` is passed.
- **Exposure:** nothing in `src/arail/` or `scripts/` passes `context=`, uses `http.client`, or calls `install_opener` (checked with grep). In the installed dependencies, only setuptools' pyproject validator uses `urlopen(context=)`, and jupyterlab's PyPI extension uses `http.client` in its own process. So this is a documentation over-claim with no pin, the same class as REVIEW W1, which scoped `requests` and `urllib` but missed these shapes.
- **Pinned now (this QA commit):** `TestQaWhereTheGuardsViewEnds::test_urllib_requests_that_do_not_go_through_the_installed_opener_are_not_checked`, 4 shapes. Mutant M2 (guard `http.client.HTTPConnection.connect`) turns all 4 red, and BY-5's opener shape with them.
- **Fix (docs):**
  - PRIVACY gap bullet: "A `urllib` request that does not go through the opener the guard installed: `urlopen(url, context=…)` (and `cafile=`/`capath=` before Python 3.13) builds its own opener; `http.client` used directly is the layer under urllib, as urllib3 is under requests; a later `urllib.request.install_opener(...)` by any library replaces the guarded opener."
  - Scope `README.md:303` and `PRIVACY.md:43-44` the same way, add one `KNOWN_GAPS` entry, and add `http.client` to the modal paragraph.
  - Closing the gap (patching `http.client.HTTPConnection.connect`, the M2 shape) is new work: file it with TD53.

### QA-W3 (WEAK, pre-existing; fix it or file it as TD60). An `allow_egress` scope opened in hybrid keeps allowing after the lab flips to airgapped

- **Where:** `src/arail/egress.py:324-326` (`_check_egress_or_raise`), `:363-366` (`GuardedHTTPAdapter.send`) and `:403-406` (`_check_httpx_url`). In all three, the allow check runs before `is_airgapped()` and ignores the mode, and `allow_egress` (`:745-753`) refuses only on entry. Contradicted claim: `docs/PRIVACY.md:151-152` ("the change takes effect immediately — no restart needed").
- **Evidence (run):** `$S/qa/probes/boundary.py`, ci (httpx2) and lock (httpx), stub transports.
  - Start in hybrid, open `with allow_egress(...)`, then flip `LAB_MODE` to airgapped (what `POST /api/airgap/toggle` does).
  - A request inside the scope passes the guard. The audit line is `reason: allow:…, lab_mode: airgapped`.
  - A task created inside the scope passes too, even when it requests after the scope has exited.
  - Every other mode change is decided per request (item 3 below).
- **Exposure:** the in-tree callers are inline provider save, test and models calls, so the window lasts one provider call. A task spawned inside a scope (the documented `create_task` leak) keeps it open indefinitely. This sprint extends the behaviour to httpx2.
- **Fix (validated, `$S/qa/fix-allow-*`):** at the three sites, `if active is not None and not is_airgapped():`. The proposed test below fails on HEAD for requests, httpx and httpx2, and passes with the fix; the 13 files stay green (ci 372, lock 372, both 421). The wider suite was not run with this fix; that is the builder's verification.
  ```python
  @pytest.mark.parametrize("client", ["requests", *installed_independently()])
  def test_an_allow_egress_scope_opened_in_hybrid_stops_allowing_when_the_lab_flips_to_airgapped(client, monkeypatch, tmp_path):
      monkeypatch.setenv("LAB_MODE", "hybrid")
      monkeypatch.setenv("ARAIL_DATA_DIR", str(tmp_path))
      monkeypatch.setattr(socket, "gethostbyname", lambda host: PUBLIC_TEST_IP)
      _make_invalid_names_unresolvable(monkeypatch)
      arail.egress._reset_for_tests()
      arail.egress.install_guard()
      url = "http://api.example-public.invalid/v1"
      call = (lambda: requests.get(url, timeout=3)) if client == "requests" else (
          lambda: importlib.import_module(client).Client(timeout=3).get(url))

      def outcome():
          try:
              call(); return "passed"
          except arail.airgap.EgressBlocked:
              return "refused"
          except Exception:
              return "passed (reached the network layer)"

      seen = {}

      async def scope_then_flip():
          with arail.egress.allow_egress("qa: a provider test in hybrid"):
              monkeypatch.setenv("LAB_MODE", "airgapped")
              monkeypatch.setenv("ARAIL_MODE", "airgapped")
              seen["inside"] = outcome()

              async def later():
                  await asyncio.sleep(0.01)
                  return outcome()

              task = asyncio.create_task(later())
          seen["task"] = await task

      asyncio.run(scope_then_flip())
      assert seen == {"inside": "refused", "task": "refused"}, seen
  ```

### NITs

- **QA-N1. A reference to the transport method taken before the guard is not checked; PRIVACY says the httpx family "has no such gap".**
  - Where: `docs/PRIVACY.md:100-101`.
  - Run: `$S/qa/probes/shapes.py` in four states (httpx2 2.16.0 with extras, httpx2 2.14.0, httpx 0.28.1 lock and both). A bound `t.handle_request`, the function `HTTPTransport.handle_request`, and a subclass whose body copies it, each taken before `install_guard()`, end in `ConnectError` with 0 audit lines. The same pre-install transport *object*, with the method looked up at call time, is refused.
  - A scan of site-packages (ci-sdk, lock-after) found no captured reference, and no third-party transport that does its own I/O apart from the documented aiohttp ones. The langchain-core SSRF, langsmith retry and openai x509 transports all forward to a real `HTTPTransport` at call time.
  - Pinned now (QA commit, caught by M1). Fix: "…patched on its classes, so every client and transport object is covered whenever it was built; a reference to the method itself taken before the guard (a bound `handle_request`, or a subclass that copies it into its class body) is not."
- **QA-N2. Classes created again after the guard are not guarded, and `coverage()`, which the doctor and the status endpoint read, still says `guarded`.**
  - Run: `boundary.py`, plus an in-process reload check. Purging and re-importing the library gives new class objects; a second `install_guard()` is a no-op; requests end in `ConnectError`. `importlib.reload(httpx2._transports.default)` does the same for a transport built from the reloaded module. `importlib.reload(httpx2)` alone stays guarded.
  - No lab code does this. Pinned now (QA commit, fresh interpreter; caught by M1 and M3).
  - Fix: next to "The guard looks at what is installed once" (`PRIVACY.md:71-72`), add "or a library imported again after the guard ran".
- **QA-N3. Child processes started with multiprocessing's `spawn` or `forkserver` method do not inherit the guard; a forked child does.**
  - Run: `$S/qa/probes/fork.py`, ci and lock. `fork` and `os.fork()` children: `EgressBlocked`. `spawn` and `forkserver` children: `ConnectError`.
  - `spawn` is the macOS default, and `forkserver` is the Linux default from Python 3.14. In-tree, `research/agenda_watch.py:525` spawns a regex-only child that makes no network call.
  - Fix: add to PRIVACY's "Other processes" (`:110-117`): "…and child processes lab code starts with multiprocessing's spawn or forkserver method (the macOS default, and Linux's from Python 3.14); a forked child inherits the guard."
- **QA-N4. Readability of the two CLI signals.**
  - The worker prints the message twice: the guard's own `_log.error`, through logging's last-resort handler, then `worker: …`. Its docstring (`nucleus/worker.py:13`) says "one line". (Run, every incomplete state.)
  - The doctor prints `egress guard : ? (EgressGuardIncomplete: …)` (`doctor.py:109`); the `?` reads as "unknown" for a state that is known.
  - Fix: before `install_guard()`, the worker gives the `arail.egress` logger a `NullHandler` with `propagate=False` (or configures logging). The doctor prints `INCOMPLETE — <message>` for `EgressGuardIncomplete`.
- **QA-N5. The remedy text can break the lab.**
  - `egress.py:601` says "install a supported version (httpx2<3, httpx<1) or uninstall httpx2". On a standard install httpx2 is the HTTP library of huggingface-hub (a core dependency), anthropic and openai, so uninstalling it disables the model downloader, and no command is given.
  - Fix: "reinstall a version the lab was tested with (for example `.venv/bin/pip install 'httpx2<3'`) and restart the lab; uninstalling httpx2 also disables the Hugging Face downloader and the cloud SDKs." `EG-A2` pins the current substring, so update it in the same commit.
- **QA-N6. Two older paragraphs still describe the old guard.**
  - `README.md:225-228` and `docs/the-lab.md:299-301` say the guard covers "`requests` and `urllib`" (no httpx or httpx2) and "loopback, RFC1918, or link-local" (REVIEW N1's imprecision). That under-claims, and it contradicts `README.md:302-305` and PRIVACY.
  - Fix: align both with `README.md:302-305`. Pre-existing and outside the guard: `PRIVACY.md:144-146` and `README.md:234-236` still describe the removed two-step confirm.
- **QA-N7. The always-visible nav badge does not reflect an incomplete guard.**
  - `#mode-badge` (`nav.js:76-81`, fed by `/api/system/mode`) stays "⬤ Airgapped" (run under node in the `unpatchable` and `lazy-attr` states). This is the builder's observation 1, which no one ruled on.
  - Fix: carry the guard state on `/api/system/mode` and paint the badge `warn` with a title, or file it.
- **QA-N8. Two test comments describe real installs inaccurately.**
  - `tests/test_egress_httpx.py:586-587` and `:633-634` say a missing httpcore2 makes the httpx2 import fail.
  - Run: `import httpx2` (2.14.0, 2.16.0) and `import httpx` (0.28.1) succeed without their httpcore. It is imported lazily, so the transport constructor fails instead, and the guard correctly reports `guarded` (harmless: no client can be built).
  - The simulation is still a valid test of the import-raises branch; only the comment is wrong. Reword it.

### Pre-existing, outside this diff, not counted in the verdict (PX-1, security; recommend fixing in this PR or filing with priority)

- **Where:** `src/arail/portal/static/nav.js:137-140`. The modal's Recent-activity rows are built with `innerHTML` from the audit log's `url_host` and `caller`. REVIEW listed this and did not count it; this adds the measurement.
- **Run:** `$S/qa/probes/xss_hosts.py` plus the node harness, ci and lock.
  - A refused `urllib.request.urlopen("http://<img src=x onerror=alert(document.cookie)>/")` stores `url_host` verbatim. The rendered row's `innerHTML` is `<code><img src=x onerror=alert(document.cookie)></code>`.
  - requests, httpx and httpx2 percent-encode the host (`%3Cimg…`), so this sprint's newly guarded library adds no route.
  - `caller` values such as `<lambda>` and `<locals>` are swallowed as unknown tags (cosmetic).
- **Reasoned, not run:** script running in the portal's origin passes the toggle's same-origin gates, so it could `POST /api/airgap/toggle`. Reaching the sink needs code in a guarded process that passes a host string containing whitespace to `urllib.request`. The in-tree `urlopen` calls use fixed or loopback URLs.
- **Fix:** build the row with `createElement` and `textContent`, as DOC-6 already does for `known_gaps` (about six lines).

**Observation (not a finding):** `HTTPTransport(uds=...)` with a non-local URL host is refused even though the socket is local, a false positive (run: `http://docker/info` refused in 0.28 s). Nothing in the tree uses `uds=`.

## What I examined, per item of the brief

Each row gives the measured result, what it means, the bar and why, what out of range would mean, and where a failure lives.

### 1. Client shapes (E5)

| Measured (run) | Means | Bar and why | Out of range | Where |
|---|---|---|---|---|
| **httpx2 2.16.0 with h2, socksio and wsproto** (`qa-extras`, `$S/qa/results/shapes-httpx2-extras.txt`): **30 refusals.** These cover an http and a socks5 proxy at loopback, `mounts`, env `HTTP_PROXY`/`ALL_PROXY`/`https_proxy`, `transport=`, a subclass calling `super()`, `http2=True`, `stream()`, three redirect second hops, `websocket()` and the ws/wss session clients. They also cover clients built before install, module-level `get()`, a worker thread, `trust_env=False`, a `127.0.0.1@host` userinfo trick, and the pre-install transport object (sync and async where applicable), at 0.00-0.28 s with 1 audit line each. **In-process passes, 5 of 5:** Mock (even for a public host), WSGI, ASGI, starlette `TestClient`, and `TestClient` with a public `base_url`. | The guard sees every shape that reaches the patched class, and leaves in-process transports alone. | Every network shape refused, every in-process shape passing: the airgap promise, without breaking the portal's own tests. | A refused shape that passes is a bypass; a blocked in-process transport would break the suite. | `egress._make_guarded_*`; EG-10 to EG-13 |
| httpx 0.28.1 (lock, both) and httpx2 2.14.0 (py310), without extras: the same, except the extras-only shapes raise the library's own `ImportError`. | Same. | Same. | Same. | Same. |
| **Not refused, all states: 3 shapes** (QA-N1). | A reference to the method taken before install is outside the class patch. | Documented and pinned. | Now pinned. | `docs/PRIVACY.md:100-101` |

### 2. The coverage boundary

| Measured (run) | Means | Where |
|---|---|---|
| Objects that exist before install: httpx-family clients, async clients and transport objects are refused; a `requests.Session` or adapter class from before is not (BY-5, documented); method references are not (QA-N1). | The class patch reaches objects, not references. | BY-5; QA-N1 pin |
| A thread started before install, requesting after it: `EgressBlocked`. A worker thread: `EgressBlocked`. | The patch is process-wide. | EG-14 |
| Fork: `os.fork()` and multiprocessing `fork` children are refused. `spawn` and `forkserver` children: `ConnectError` (QA-N3). | The guard is per interpreter. | `$S/qa/results/fork-*.txt` |
| A module purged and re-imported, or a reloaded transport module: unguarded, with `coverage()` stale (QA-N2). `importlib.reload(httpx2)` alone: still guarded. `importlib.reload(arail.egress)` then re-install: still refused (the old wrappers hold), but `_PATCHED` is empty, so a later reset cannot restore anything (test concern only). | The guard keeps one snapshot of the classes it saw. | QA-N2 pin |
| Reset and reinstall: covered by EG-16, EG-17 and EG-A5; not re-probed. | n/a | EG-17 |
| Maximus import (`lock-after`: anthropic 0.120.0, langchain-core 1.5.1, langsmith 0.10.10) and ci: importing the portal leaves **0** `requests.Session`, **0** stock adapters, **0** `OpenerDirector` objects and **0** stock-adapter subclasses before start-up installs the guard (`$S/qa/probes/preinstall_objects.py`). | REVIEW W1's "no live path in the portal today" also holds on maximus, where it had been measured only on ci and lock. | `portal/app.py` `_startup` |
| urllib beyond the installed opener (QA-W1); `http.client` (QA-W1). | Not documented. | Pinned now. |
| Docs compared: PRIVACY `:43-44`, `:96-101`, `:71-72`, `:110-117`; README `:302-303`; `KNOWN_GAPS`. | Contradicted by QA-W1 and QA-N1, incomplete per QA-N2 and QA-N3. | Builder list |

### 3. Mode changes

| Measured (run, `$S/qa/results/boundary-*.txt`) | Means | Bar and why | Out of range | Where |
|---|---|---|---|---|
| Sync and async clients and a `requests.Session` built before a flip follow the new mode on their next request, hybrid to airgapped and back; so does `urlopen`. `LAB_MODE=''` falls back to `ARAIL_MODE=hybrid`; `' HYBRID\n'` counts as hybrid; a typo counts as airgapped (fail closed). | The decision is taken per request. | Per request, because the toggle writes `os.environ` in the running portal. | A client that keeps the old mode means the decision was cached. | `airgap.lab_mode`; **new test** `test_lab_mode_is_read_per_request_by_clients_that_already_exist` |
| Mutant M4 (decision frozen at install) is caught by **only** that new test across all 13 CI files (2 ids). | Before this QA commit, nothing in CI pinned per-request mode for existing clients. | At least one red id per mutant. | | `$S/qa/results/mutant-M4-all13.txt` |
| An `allow_egress` scope opened in hybrid survives a flip (QA-W3). | One exception to "per request". | | | QA-W3 |

### 4. Failure states, through the real endpoint and the real front end

The real `_install_egress_guard()` and `get_airgap_status()` were called directly (no server, no port); `nav.js` ran under node with a DOM stub on that JSON (`$S/qa/probes/nav_harness.js`). The doctor and the worker ran as `python -m`, through runpy, in the simulated state.

| State (venv) | `/api/airgap/status` | `nav.js` modal | Doctor (E12) | Worker | True, readable, no traceback? |
|---|---|---|---|---|---|
| Complete (ci, lock, both, py310, clean) | `guard_installed` true; definition "…blocked."; 5 gaps | pill `ok`; list closed; flips keep the right colour | `installed — guards requests, urllib.request, httpx2 (not installed: httpx)` (and the per-venv variants); every member listed with its version | reaches the phase | yes |
| Library absent (neither) | true; unqualified; 5 gaps | `ok` | `installed — guards requests, urllib.request (not installed: httpx2, httpx)` | reaches the phase | yes |
| Installed, httpcore missing (realistic broken install) | true; `httpx2: guarded` | `ok` | `guarded` | n/a | yes. It cannot be used, so no hole (QA-N8). |
| Installed, import raises `ModuleNotFoundError` (ci, both) | false; "…EXCEPT requests made through httpx2…"; gap entry naming the import error | pill `warn` with tooltip; list opened with the entry; stays amber across flips | `? (EgressGuardIncomplete: … import raised ModuleNotFoundError: No module named '…'…)`, exit 3 | the message twice, exit 1 (QA-N4) | yes, apart from QA-N4 and QA-N5. In both, httpx stays guarded. |
| Installed but unpatchable (`AsyncHTTPTransport` deleted) | false; qualified; entry | `warn`, opened, entry | `? (EgressGuardIncomplete: … AsyncHTTPTransport.handle_async_request is missing…)` | twice, exit 1 | yes, apart from QA-N4 and QA-N5. Sync httpx2 is refused and async reaches the network, as REVIEW B1 found. |
| Attribute lookup raises (both) | false; **unqualified**; **no entry** | `warn`, but the list names nothing | `? (ImportError: …)` | **traceback** | **no: QA-W2** |

### 5. SDK evidence (E4)

| Measured (run) | Means | Bar and why | Out of range | Where |
|---|---|---|---|---|
| **anthropic 2.5.0** (ci-sdk, httpx2 2.16.0), built before and after install: final `APIConnectionError <- EgressBlocked`. `max_retries=0`: **1** refusal, 0.00-0.10 s. Defaults: **3** refusals, 1.29-1.39 s. | The SDK wraps the refusal and retries it with backoff. | `EgressBlocked` in the chain, no network error type, exactly 1 refusal at `max_retries=0`. That proves no attempt left; the default retry count is the SDK's own business. | `ConnectError` or `gaierror` in the chain would mean the default client left the guarded transports. | `$S/qa/results/sdk-ci-sdk.txt`; SDK-1 |
| **openai 3.46.0**: final `EgressBlocked`, **1** refusal with and without retries, 0.00-0.03 s. | No retry on a refusal. | Same. | Same. | Same. |
| **anthropic 0.120.0 on httpx 0.28.1** (lock-after, the lock version REVIEW did not re-run): same as 2.5.0 (1 and 3 refusals, 1.30-1.41 s). openai is not in the maximus lock. | Same. | Same. | Same. | `$S/qa/results/sdk-lock-after.txt` |

### 6. The clean-machine path (`pip install -e .` only, no dev extra, no SDKs: `qa-clean`, httpx2 2.16.0, huggingface-hub 2.4.0, no pytest)

| Measured (run) | Means | Bar and why | Out of range | Where |
|---|---|---|---|---|
| The brief's probe, literal, fresh interpreter: `EgressBlocked` in 0.055 s. httpx is not installed, so there is nothing to probe for it. | Refused before any socket I/O. | `EgressBlocked` for each installed library: the airgap promise on a fresh install. | Any other exception means the library is unguarded. | FR-1 |
| `install_guard()` added cost (E11), 7 fresh interpreters: median **14.1 ms** (12.1-15.2), measured while the orchestrator's full suite ran. Cold `import arail.egress`: 70-78 ms. httpx2 import after it: 10.6 ms cumulative. | What the portal, doctor and worker start pay for eager import. | Under 50 ms per library: the portal starts in seconds, and the doctor and worker are CLIs. | Near 50 ms, revisit lazy import for the CLI paths. | `egress._install_httpx_family_guard` |
| Doctor: `egress guard : installed — guards requests, urllib.request, httpx2 (not installed: httpx)`, with every family member listed. Its exit 3 comes from the scratch lab's provisioning findings; `egress_guard` is ok. | An operator sees what is covered. | E12. | "httpx: not installed" with no httpx2 line would mean the old list. | `doctor.check_environment` |
| Portal start-up function plus status: complete, unqualified; httpx2 sync and async `EgressBlocked`. huggingface_hub 2.4.0 `model_info`, `hf_hub_download` and `snapshot_download`: `EgressBlocked`, 1 refusal each, at most 0.11 s (lock, hf 1.25.1: same, at most 0.125 s). | The model downloader is stopped on the first attempt. | Exactly 1 refusal, under 1 s, so no retry budget is spent. | More than 1 means hf started retrying a `RuntimeError`. | FR-5 |

### 7. Regression

| Measured (run) | Means | Bar and why | Out of range | Where |
|---|---|---|---|---|
| **E13**, the 13 `EGRESS_TESTS` files in one process, in a scratch export of HEAD, `CI=true`, declared. Forward and reversed: ci 362/362, lock 362/362, both 406/406, py310 362/362; 0 failed, 0 skipped; the workflow's checker accepts all 8 runs. With the QA tests: 372/372/421/372, same both ways. | Patch state does not leak between files. | Identical counts both ways, because the suite shares one process. | A difference names the file whose reset is incomplete. | `$S/qa/results/e13*-*.check` |
| **All five legs** (`emulate.py`, the workflow's own `run:` text) on the worktree **with** the QA additions. Legs 1-3: 372 cases each; leg 4: 421; checker "none skipped, failed or missing". Leg 5: sentinel failed with `UNPROVEN`, declaration passed. SDK step: 3 cases on anthropic 2.5.0 and openai 3.46.0. 0 verdict-like lines in the green logs. | The CI contract holds after this commit. | As CI's own checker. | A skip, a failure or a missing file is red. | `.github/workflows/egress-guard.yml` |
| **Neighbours** (12 files outside the 13 that read the status endpoint, the doctor, `install_guard` or the worker), one process per file, ci and lock-after: failing ids identical to the orchestrator's baseline 3 on `origin/main` `de1ce285`. One pre-existing failure, in `test_ensure_ready_build_isolation.py`, on both sides. | No neighbour changed verdict. | Same failing ids as main. | A new failing id is a regression until a serial re-run says otherwise. | `$S/qa/results/neigh-*` |
| **`tests/test_queuellm_rename_frozen_surface.py`:** 2 failed, 3 passed on HEAD (ci and lock) and on `origin/main`. The 13 `E ` lines are **identical** after normalising the tree path, also after the QA commit. | The pre-existing failures hide no new one. | The text must not change (it is red on main). | A changed line names the frozen token or path. | That test's `E ` lines |

### Happy path (10%)

The brief's literal probe, fresh interpreter, every venv: **10 of 10** installed cells end in `EgressBlocked` with exactly 1 audit line, in 23-70 ms (ci, py310, lock, both, qa-clean, qa-extras, ci-sdk, lock-after; httpx2 2.14.0 and 2.16.0, httpx 0.28.1). The neither venv has nothing to probe.

## Test inventory (added in this QA commit, `tests/test_qa_airgap_bypass_attempts.py`, `TestQaWhereTheGuardsViewEnds`)

| # | Test | Category | Covers | Status |
|---|---|---|---|---|
| Q1 | `test_a_reference_to_the_transport_method_taken_before_the_guard_is_not_checked[3 shapes × lib]` | edge, coverage boundary (gap pin) | QA-N1; the covered contrast (an object, looked up now) is asserted too | pass in all states; red under M1 |
| Q2 | `test_urllib_requests_that_do_not_go_through_the_installed_opener_are_not_checked[4 shapes]` | edge, coverage boundary (gap pin) | QA-W1; contrast: the installed opener is refused | pass; red under M2 |
| Q3 | `test_a_library_whose_classes_are_created_again_after_the_guard_is_not_guarded[lib]` | edge (gap pin, fresh interpreter) | QA-N2, including `coverage()` staleness | pass; red under M1 and M3 |
| Q4 | `test_lab_mode_is_read_per_request_by_clients_that_already_exist[requests, lib]` | closed path, mode change | item 3; the only test red under M4 | pass; red under M4 |

Cases added: ci, lock and py310 +10; both +15. In neither, 5 run and the 4 library-parametrized cases skip for an empty parameter set; leg 5 does not run this file. All pass in ci, py310, lock, both, neither, qa-extras, ci-sdk and lock-after. Not committed, because they fail until fixed: the QA-W2 and QA-W3 tests above.

**Mutants** (scratch copies of the export, never the worktree; `$S/qa/mutants.py`, ci venv; control 0 failing ids):

| Mutant | New failing ids |
|---|---|
| M1: the pool layer under the httpx family is guarded too | 6: Q1 ×3, Q3, BY-3 and BY-6 |
| M2: `http.client` connections are guarded | 5: Q2 ×4 and BY-5's opener shape |
| M3: `install_guard` looks again on every call | 2: Q3 and BY-6 |
| M4: the airgapped decision is frozen at install | 2: Q4 ×2, and nothing else in the 13 files |

## Security review (what was checked, by name)

| Surface | Checked | Findings |
|---|---|---|
| Airgap enforcement, httpx family | 30 shapes refused, 5 in-process pass, 3 states; SDKs on two newer releases and one lock release; huggingface_hub 2.4.0 and 1.25.1 | QA-N1 (references), QA-N2 (re-created classes) |
| Airgap enforcement, stdlib | `urlopen` with and without `context`/`cafile`, `urlretrieve`, `http.client`, `xmlrpc.client`, `install_opener` | QA-W1 |
| Failure states (fail-loud contract) | 6 states × endpoint, `nav.js`, doctor and worker | QA-W2 |
| Mode changes | Both directions, sync, async, sessions, urllib, the env fallback chain, allow scopes | QA-W3 |
| Process boundary | fork, spawn, forkserver; maximus import-time objects (0 found) | QA-N3 |
| Output encoding (audit to UI) | Hosts with markup through all four clients; `innerHTML` rows | PX-1 (pre-existing) |
| Secrets | Not re-run: EG-1 and REVIEW's R2 and R3 already prove a `?token=` stays out of the audit line and the exception. The QA-W2 error text carries import-error text only. | none new |
| Dependencies | None added (checked: `pyproject.toml` and `uv.lock` unchanged on the branch). The SDK step installs the latest SDKs in a CI-only venv. | none |
| Crypto, auth, deserialization | Not touched by this change | n/a |

## Performance

| Measured (run) | Means | Bar and why | Out of range | Where |
|---|---|---|---|---|
| Per-request cost the wrapper adds (stubbed transport, 5 × 2,000 calls, median): **+2.7 µs** for an IP literal (`127.0.0.1`) and **+67.6 µs** for `localhost` (one `gethostbyname`, about 63 µs here). | The cost of the locality check on every httpx-family request. | Small next to any real request; the lab's own Ollama traffic goes through `requests`, which already paid this. | Milliseconds per request on `localhost` would mean a slow resolver: TD59 (REVIEW N6). | `airgap.is_local_host` |
| E11 (above): 14.1 ms median. | Start-up cost. | Under 50 ms. | | |

No BENCHMARK.md: this is not a hot-path change beyond what lock installs already ran for httpx.

## Coverage delta

Line and branch coverage of the guard modules, in-process only (fresh-interpreter children are not measured), the 13 files, ci venv, `coverage` 7.17.2 in my own `qa-extras` venv:

- Before: `egress.py` 83% (366 statements, 63 missed; 88 branches, 10 partial); `airgap.py` 97%; `httpx_family.py` 97%.
- After: the same. The QA tests pin behaviour at the boundary (stdlib paths, references, mode), not new lines. M4 shows what they add.
- Lines in `egress.py` that no CI test reaches include the allow-scope branches of the urllib and requests paths (`:326`, `:365-366`) and the requests bootstrap branch (`:371-372`). These are exactly the branches QA-W3's fix touches.

## What I could not verify here

- Anything on a GitHub-hosted Linux runner: the install steps, pip's resolution on the day (leg 2's httpx2 on Python 3.10), `uv sync --frozen` on Linux, and the schedule.
- A real browser rendering the modal. `nav.js` ran under node with a DOM stub; PX-1's script execution and the toggle chain are reasoned from the `innerHTML` string, not executed.
- A pip-floating maximus install with both libraries (anthropic 2.x, openai 3.x and LangChain together); the `both` venv (dev plus httpx) is the closest state I had.
- The extras-only shapes (socks, http2) on httpx 0.28.1; the lock venv has no h2 or socksio. The architect measured them.
- The AirLLM worker's hub fallback (airllm is not installed); Python 3.12 and 3.13.
- The full suite. That is the orchestrator's after-run, which finished while this pass ran; I did not read or judge its comparison.
- The QA-W3 fix against the wider suite.

## The exact list the builder must change

1. **QA-W2 (before merge):** `src/arail/egress.py:515`. Wrap the `_patch_family_member` call as shown in QA-W2, and add the proposed test to `tests/test_egress_httpx.py` next to EG-A3. Re-run: the 13 files in ci, lock, both and py310, and `state_status.py lazy-attr` in both (httpx must be `EgressBlocked`).
2. **QA-W1 (docs):**
   - `docs/PRIVACY.md`: the new gap bullet as written in QA-W1, plus the scope at `:43-44` and `:96-99`.
   - `README.md:303`: scope "the default opener" to "`urlopen` without an SSL context".
   - `src/arail/airgap.py` `KNOWN_GAPS`: one entry naming `http.client` and urllib requests that do not use the installed opener.
   - The modal paragraph: name `http.client`.
   - The pins are already committed (Q2).
3. **QA-W3:** either change the three sites (`egress.py:325`, `:364`, `:404`) to `if active is not None and not is_airgapped():` and add the proposed test, or file TD60 with QA-W3's text in commit 8.
4. **NITs, at the builder's discretion in the same pass:**
   - PRIVACY wording: QA-N1 (`:100-101`), QA-N2 (`:71-72`) and QA-N3 (`:110-117`).
   - The worker's duplicate line and the doctor's `?` (QA-N4).
   - The remedy text and its EG-A2 pin (QA-N5).
   - `README.md:225-228` and `docs/the-lab.md:299-301` (QA-N6).
   - The badge (QA-N7, or file it).
   - Two test comments (QA-N8).
5. **PX-1** (pre-existing): build the Recent-activity row in `nav.js:137-140` with `textContent`, or file it with priority in commit 8.
6. **Commit 8** (orchestrator): also file whichever of the above are not fixed.

## Notes for the next QA pass

- The guard's view is a snapshot: of class objects (QA-N1, QA-N2), of one urllib opener (QA-W1), and of the libraries installed at start (W3 and TD57). A test that changes any of these after install is the shape to look for.
- Fail-loud paths are pinned for import errors and missing classes, but not for exceptions raised elsewhere inside the family step. Any new code between "installed" and "patched" needs its own containment test (QA-W2).
- The decision functions exist in three copies (adapter, `_check_egress_or_raise`, `_check_httpx_url`). QA-W3's fix and any future rule must change all three; one shared helper would remove the drift risk.
- `inspect.stack()` in `_current_caller` reads source lines for every frame on each refusal. That is cheap today, but worth a number if refusals become frequent (an SDK's default retries write 3 lines per call).
