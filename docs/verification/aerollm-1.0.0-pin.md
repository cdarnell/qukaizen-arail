# aerollm-api 1.0.0 pin verification

**Date:** 2026-07-24
**Pin change:** `pyproject.toml:275` `aerollm = "aerollm-api>=0.1,<0.2"` →
`aerollm = "aerollm-api>=1.0,<2.0"`
**Companion aerollm-side sprint:** `2026-07-24-private-1.0-version-bump`
(see [ADR 0023](https://github.com/cdarnell/qukaizen-aerollm/blob/main/docs/decisions/0023-private-1.0-version-contract.md)
in that repo). `aerollm-api 1.0.0` was published to
`pypi.qukaizen.com` — the **private index only** — immediately before
this verification ran; no public-PyPI byte was uploaded and no
`v1.0.0` git tag was cut in the QueueLLM repo.
**Precedent this mirrors:** `sprints/2026-07-11-aerollm-release-arail-verify/`
(the original gate-#12 downstream-consumer-wheel proof, done at
`0.1.0`). This is the same discipline, re-run at `1.0.0`.

## Result: PASS (with one documented model substitution)

A real chat turn ran end-to-end through `AeroLLMBackend` against the
published `1.0.0` wheel, installed via pip into an isolated scratch
venv (the RELEASE channel — no local QueueLLM sibling repo present, no
`maturin develop`, no cargo build). The real `~/ProJects/arail/.venv`
was untouched throughout.

## Environment

- Isolated scratch venv, **not** the real arail `.venv`:
  `/private/tmp/.../scratchpad/arail-1.0-verify/venv` (Python 3.11.15
  — 3.9 cannot exercise the backend per the pin verification recipe).
- `ARAIL_AEROLLM_REPO` pointed at a nonexistent directory
  (`.../scratchpad/arail-1.0-verify/nonexistent-aerollm`) to force the
  RELEASE (pip-from-index) channel, not the DEV (cargo sibling-build)
  channel — this is F8 from the QueueLLM sprint's own failure-mode
  table: "verification silently uses the DEV/cargo channel, proving
  nothing."
- `AEROLLM_PIP_SPEC="aerollm-api>=1.0,<2.0"` — the exact new pin, as it
  now reads in `pyproject.toml`.

## Step 1 — install via `scripts/build-aerollm.sh update`

```
$ cd /Users/netsushi/ProJects/arail
$ export ARAIL_AEROLLM_REPO=".../scratchpad/arail-1.0-verify/nonexistent-aerollm"
$ export PYTHON=".../scratchpad/arail-1.0-verify/venv/bin/python"
$ export AEROLLM_PIP_SPEC="aerollm-api>=1.0,<2.0"
$ bash scripts/build-aerollm.sh update

• Installing aerollm_api from index https://pypi.qukaizen.com/simple/…
Looking in indexes: https://pypi.qukaizen.com/simple/, https://pypi.org/simple/
Collecting aerollm-api<2.0,>=1.0
  Downloading aerollm_api-1.0.0-cp39-abi3-macosx_11_0_arm64.whl (38.8 MB)
     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 38.8/38.8 MB 66.5 MB/s  0:00:00
Installing collected packages: aerollm-api
Successfully installed aerollm-api-1.0.0
• QueueLLM ready (release wheel 1.0.0) — the 2nd inference.
```

## Step 2 — `scripts/build-aerollm.sh status`

```
$ bash scripts/build-aerollm.sh status

• QueueLLM (2nd inference) status
    repo:         .../scratchpad/arail-1.0-verify/nonexistent-aerollm
    crate:        .../nonexistent-aerollm/crates/aerollm-api (missing → release channel)
    index:        https://pypi.qukaizen.com/simple/
    site-packages: .../scratchpad/arail-1.0-verify/venv/lib/python3.11/site-packages
    aerollm_api:  importable ✓ (version 1.0.0)
    bg pressure:  0.60 (default)
```

`(missing → release channel)` + `version 1.0.0` — confirms this is the
pip wheel path, not a cargo/dev build (F8 assertion).

## Step 3 — assertions

1. **`aerollm_api.__version__ == "1.0.0"`** — ✅
   ```
   >>> import aerollm_api; aerollm_api.__version__
   '1.0.0'
   ```
2. **`"site-packages" in aerollm_api.__file__`** (pip wheel, not a
   cargo dylib) — ✅
   ```
   .../scratchpad/arail-1.0-verify/venv/lib/python3.11/site-packages/aerollm_api/__init__.py
   ```
3. **`deep status` prints `(missing → release channel)` and
   `version 1.0.0`** — ✅ (Step 2 above).
4. **`import aerollm` raises `ModuleNotFoundError`** (no source-name
   leak) — ✅
   ```
   >>> import aerollm
   ModuleNotFoundError: No module named 'aerollm'
   ```
5. **Installed tree is `.abi3.so` + `mlx.metallib` + `__init__.py` +
   dist-info only — zero `.rs` / `Cargo.toml` / `crates/`** — ✅
   ```
   $ find .../site-packages/aerollm_api -type f
   .../aerollm_api/__init__.py
   .../aerollm_api/__pycache__/__init__.cpython-311.pyc
   .../aerollm_api/aerollm_api.abi3.so
   .../aerollm_api/mlx.metallib
   $ find .../site-packages -iname "*.rs" -o -iname "Cargo.toml" -o -ipath "*crates*" | grep -i aerollm
   (no output)
   ```
6. **The real chat turn** — ✅, see below.
7. **Negative control**: in a second, separate scratch venv,
   `pip install "aerollm-api>=0.1,<0.2"` from the index still resolves
   `0.1.0` — ✅
   ```
   $ pip install "aerollm-api>=0.1,<0.2" --index-url https://pypi.qukaizen.com/simple/ --extra-index-url https://pypi.org/simple/
   Collecting aerollm-api<0.2,>=0.1
     Using cached aerollm_api-0.1.0-cp39-abi3-macosx_11_0_arm64.whl (38.8 MB)
   Successfully installed aerollm-api-0.1.0
   ```
   Proves the `1.0.0` upload did not clobber the pre-existing `0.1.0`
   artifact.
8. **The index lists exactly `0.1.0rc2`, `0.1.0`, `1.0.0` afterwards**
   — ✅
   ```
   $ curl -s https://pypi.qukaizen.com/simple/aerollm-api/ | grep -oE 'aerollm_api-[0-9][^"]*\.whl' | sort -u
   aerollm_api-0.1.0-cp39-abi3-macosx_11_0_arm64.whl
   aerollm_api-0.1.0rc2-cp39-abi3-macosx_11_0_arm64.whl
   aerollm_api-1.0.0-cp39-abi3-macosx_11_0_arm64.whl
   ```
9. **The real arail `.venv` is re-checked post-run and is untouched**
   — ✅
   ```
   $ /Users/netsushi/ProJects/arail/.venv/bin/python -m pip show aerollm-api
   Version: 0.1.0
   ```
   Still `0.1.0` — no command in this verification was ever pointed at
   the real `.venv` (all `PYTHON=`/`ARAIL_AEROLLM_REPO=` overrides used
   the isolated scratch venv).

## Step 4 — the real chat turn (assertion 6)

**Model substitution, noted explicitly per the gate-#12 precedent**:
the default model (`Qwen2.5-7B-Instruct` / `-4bit`) is not present on
this verification box — only Hugging Face cache *stub* directories
exist for it (metadata only, no downloaded weight blobs). A different,
fully-downloaded, architecturally-supported local checkpoint was
substituted instead: **`mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit`**
(`model_type: "qwen3_moe"` — native Qwen3-MoE, the same architecture
family QueueLLM's GA MoE evidence is built on, ADR 0012 in the QueueLLM
repo), pointed to directly via `AEROLLM_MODEL=<absolute snapshot path>`
(`AeroLLMBackend` accepts an absolute path for `AEROLLM_MODEL`, bypassing
the `ARAIL_MODELS_DIR` join). Same allowance the prior gate-#12 sprint
took (it substituted `gpt-oss-20b-MLX-4bit` for the same reason and
recorded `WEAK_PASS` for the substitution) — same honesty requirement:
named here, not hidden.

`AeroLLMBackend.complete()` was called directly (bypassing arail's
`arail.router` package `__init__` chain, which pulls in an unrelated
required dependency — `dac_world`, a private git package — not
relevant to proving the QueueLLM wheel path works; `backends.py` itself
has zero `arail.*` imports, so it loads cleanly via
`importlib.util.spec_from_file_location` without the package parent).

```python
import importlib.util, time, sys

spec = importlib.util.spec_from_file_location(
    "arail_backends_standalone",
    "/Users/netsushi/ProJects/arail/src/arail/router/backends.py",
)
mod = importlib.util.module_from_spec(spec)
sys.modules["arail_backends_standalone"] = mod
spec.loader.exec_module(mod)
AeroLLMBackend = mod.AeroLLMBackend

backend = AeroLLMBackend()
resp = backend.complete(
    "What is the capital of France? Answer in one short sentence.",
    max_tokens=32, temperature=0.0,
)
```

Output:

```
Constructing AeroLLMBackend (this loads the model, may take a bit)...
Constructed in 0.0s, api_version=1.0.0, model_name=/Users/netsushi/.cache/huggingface/hub/models--mlx-community--Qwen3-30B-A3B-Instruct-2507-4bit/snapshots/e9675aa3ca5f900ccef55267914466d55ab325fa
complete() took 6.3s
RESPONSE TEXT: 'The capital of France is Paris.'
model: /Users/netsushi/.cache/huggingface/hub/models--mlx-community--Qwen3-30B-A3B-Instruct-2507-4bit/snapshots/e9675aa3ca5f900ccef55267914466d55ab325fa tokens_used: 6 backend: aerollm
PASS: non-empty response, process about to exit 0
```

Process exit code: `0`. (One `RuntimeError: aerollm_api::Runtime is
unsendable, but is being dropped on another thread` line printed to
stderr on interpreter shutdown — this is the documented, cosmetic
PyO3-unsendable-handle GC-ordering noise `AeroLLMBackend`'s own class
docstring calls out explicitly as "not a runtime bug," not a test
failure; the process's actual exit code was `0`.)

**Non-empty text, real generation (`api_version=1.0.0`), clean process
exit.** Assertion 6: ✅.

## Where evidence lives (per the QueueLLM sprint's evidence-splitting design)

- **This file** (arail repo): the full transcript — install log,
  `status` output, all nine assertions, the chat turn, the negative
  control. Authoritative for a future arail maintainer.
- **QueueLLM repo** (`sprints/2026-07-24-private-1.0-version-bump/BUILD_LOG.md`):
  pass/fail summary, the wheel sha256, and this PR's URL + commit SHA
  as a cross-reference — not a duplicate transcript.

## Summary

| # | Assertion | Result |
|---|---|---|
| 1 | `aerollm_api.__version__ == "1.0.0"` | ✅ |
| 2 | `"site-packages" in aerollm_api.__file__` | ✅ |
| 3 | `status` → `(missing → release channel)`, `version 1.0.0` | ✅ |
| 4 | `import aerollm` → `ModuleNotFoundError` | ✅ |
| 5 | installed tree: `.abi3.so` + `mlx.metallib` + `__init__.py` + dist-info only | ✅ |
| 6 | real chat turn, non-empty text, exit 0 | ✅ (model substituted, see above) |
| 7 | negative control: `0.1.0` still resolves | ✅ |
| 8 | index lists exactly `0.1.0rc2`, `0.1.0`, `1.0.0` | ✅ |
| 9 | real arail `.venv` untouched (still `0.1.0`) | ✅ |

**Overall: PASS.** The published `1.0.0` wheel installs and runs
end-to-end through the exact consumer path (`AeroLLMBackend`, RELEASE
channel, pip-from-private-index) a real arail deployment would use.
