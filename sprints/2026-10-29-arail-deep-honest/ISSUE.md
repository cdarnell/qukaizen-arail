# DRAFT: QueueLLM issue (not filed)

**Status:** draft. The operator reads it and files it (O3). Nothing here has been posted.
**Where it goes:** `cdarnell/qukaizen-queuellm`.
**Facts come from:** this sprint's `DAY1.json` (2026-10-29) and Buddy S6's `DAY1.json` and `BUILD_LOG.md` (2026-10-27). No lab data, no World names, no term names.

## Title

Qwen2.5-7B-Instruct-4bit: the runtime answers once per process; every later generate fails in prefill with [rms_norm] … has 1 elements

## What happens

One process loads `Qwen2.5-7B-Instruct-4bit` and starts the runtime. The first `generate` answers. Every later `generate` in that process fails in under 0.02 s, in the first prefill step. The prompt length does not matter (33 and 1,933 tokens both fail). Repeating call 1 word for word fails too.

The error, as recorded:

```
RuntimeError: backend `mlx-native` failed: apply_pre_block: Qwen2 layer (chunked prefill head): "[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements.
```

## S6's text, verbatim

Buddy S6 `BUILD_LOG.md`, "Architect feedback required", item 2 (2026-10-27):

> Qwen2.5-7B-Instruct-4bit, `Runtime.generate` works once per process; the second call fails in `apply_pre_block: Qwen2 layer (chunked prefill head)` with `[rms_norm] (*weight) must have the same size as the last dimension of x but has 1 elements`. Reproduced on aerollm_api 0.1.0 builds from 2026-06-28, 2026-07-22 and 2026-08-05; main `e730d5fe` could not be built here (Metal Toolchain unusable). Probe: one cold call of 67 to 110 tokens, then four failures.

## Update, 2026-10-29

Main `e730d5fe3f6b1d4ff4c93e9eade4bc7afcc64715` now builds on the same Mac (the Metal toolchain answers again). It passes QueueLLM's preamble check (0 `downloadComponent` in the artifact). It still answers 1 of 5. So do the 2026-10-11 build and ARAIL's bundle v1.1.0.

## Seven runs, six binaries

Rows 1 and 4 are the same installed file, by path and by date. Buddy S6 did not record a hash, so that is not proven.

| # | When | What was loaded | sha256 of the native file | Version it reports | Call 1 | Error wording at call 2 |
|---|---|---|---|---|---|---|
| 1 | S6, 2026-10-27 | the install in ARAIL's venv (modified 2026-06-28), read only | not recorded | 0.1.0 | 19.2 s | `Qwen2Model::forward (prefill head)` |
| 2 | S6, 2026-10-27 | `dist/*.whl` in the QueueLLM checkout (2026-07-22), unzipped to scratch | not recorded | 0.1.0 | 68.4 s | `Qwen2 layer (chunked prefill head)` |
| 3 | S6, 2026-10-27 | `target/release/libaerollm_api.dylib` in the QueueLLM checkout (2026-08-05), copied to scratch | not recorded | 0.1.0 | 68.6 s | `Qwen2 layer (chunked prefill head)` |
| 4 | 2026-10-29 | the same install in ARAIL's venv (21,894,128 bytes, modified 2026-06-28). Its source commit is not recorded. | `d62e1e16e93c264b79eae35e5766a84f56091ed8e4cc66a89e18791effc7579e` | 0.1.0-rc.2 | 9.65 s | `Qwen2Model::forward (prefill head)` |
| 5 | 2026-10-29 | `libqueuellm_api.dylib` built 2026-10-11 11:13 in a worktree of branch `qukaizen/queuellm-gpt-oss-fix`. The branch head is `25dc9aab` (committed later that day, 21:42), so the exact source commit of the build is not recorded. Copied to scratch as `queuellm_api.abi3.so`. | `f92a4cdd1af06a8f90287295131a3588fe3896dca90a851a1fdd8705ac719fc4` | 1.1.0 | 17.61 s | `Qwen2 layer (chunked prefill head)` |
| 6 | 2026-10-29 | ARAIL release bundle v1.1.0 (QueueLLM `2ae56afb3f8807c5df29daccf37ffb2413966f51`). Tarball sha256 `57f30364738580d4a7808f9165ddfdf692eb52afaa435e491b4eda39afb63e93`, extracted to scratch. | `7c77aa8511d019a320374f539608ca073a4d5e019ae1e58b0102b922cb360ce7` | 1.0.0 | 17.53 s | `Qwen2 layer (chunked prefill head)` |
| 7 | 2026-10-29 | QueueLLM main `e730d5fe3f6b1d4ff4c93e9eade4bc7afcc64715`, exported with `git archive`, built offline in a scratch target dir: `cargo build --release --offline -p queuellm-api --features extension-module`, 1 min 57 s | `d7e5aa57929d23fea49f6297f53bead3df3b74a6414a22f55ef46159bd074fd8` | 1.1.0 | 20.65 s | `Qwen2 layer (chunked prefill head)` |

On main the module is `queuellm_api`. ARAIL imports the frozen name `aerollm_api`. So rows 5 and 7 were loaded through a one-line scratch package, `aerollm_api/__init__.py`, that does `from queuellm_api import *`.

### How to read the numbers

- **Call 1** is the wall time of the one call that answered: prefill plus up to 384 new tokens. It is not a benchmark. S6 asked one question five times. The 2026-10-29 runs mix short and long prompts. So rows 1 to 3 and rows 4 to 7 do not compare with each other.
- **Calls answered, 1 of 5**, is the result that matters. The bar is 5 of 5, because every real consumer makes more than one call per process. Anything below 5 of 5 means the runtime answers once per restart.
- **Failed calls took 0.00 to 0.02 s.** That means the error is raised before any decoding, in the first prefill step. It points at state the runtime carries from call 1 into call 2 inside one process.
- **Prompt tokens** were 33, 1,933, 33, 1,933 and 34 (ARAIL's chat wrap, counted by the model's tokenizer). The long prompt is the size of a chat turn with lab context.

## Reproduction

ARAIL's `AeroLLMBackend` (`src/arail/router/backends.py`) builds the runtime, starts it, then generates. This is a sketch of those calls. It is not a separately tested script.

```python
import aerollm_api            # queuellm_api on main; ARAIL loads it through a shim
rt = aerollm_api.Runtime(model_path, kv_memory_budget=kv_bytes, ring_depth=2)
rt.start()
for prompt in five_chat_wrapped_prompts:
    rt.generate(prompt, max_new_tokens=384, temperature=0.2)
```

Once ARAIL's `deep probe` is merged, `./arailctl deep probe` runs exactly this through ARAIL's own backend, in a fresh process, and prints how many of the 5 calls answered and where the first error is. The probe's prompt set is pinned by hash (`b5bcb562d908f5d8c843cfe0b0461bfdeb39933339b45e332e91120f7918641e`), so a result can be compared with this table.

## Observations

- Call 2 fails at the first prefill step, whatever the prompt length, and even when it repeats call 1.
- The June build words the error `Qwen2Model::forward (prefill head)`. The later builds say `Qwen2 layer (chunked prefill head)`. The failing check is the same `[rms_norm]` weight-size check.
- All four runs on 2026-10-29 used ring depth 2 (ARAIL's `balanced` profile). ARAIL's own overnight failures, logged between 2026-08 and 2026-09 in a background lab, ran under a different profile, probably ring depth 4. That is an inference, not a measurement.

## Environment

- Apple M5 Max, 36 GB.
- Darwin 25.6.0.
- `xcrun -sdk macosx metal --version`: Apple metal version 32023.921 (exit 0 on 2026-10-29; it failed on 2026-10-27 with no reboot between, cause not known).
- Python 3.11.
- Model: `Qwen2.5-7B-Instruct-4bit` (MLX 4-bit, about 4 GiB on disk).

## Context and the question

ARAIL's deep slot runs a dense Qwen2.5-7B through QueueLLM. QueueLLM is MoE-exclusive and lists Qwen2-dense as a legacy lane (QueueLLM `CLAUDE.md:49-63`).

**Is a fix planned for this lane, and if so, roughly when?**

ARAIL does not need a fix to ship what it is shipping: a probe and status surfaces that say plainly when deep generation fails, and a rule that stops background work from using the deep model until a probe passes. ARAIL will decide on 2026-11-19 whether to keep QueueLLM in the deep slot, move the slot to a model QueueLLM invests in, or take QueueLLM out. A yes or no and a rough date is enough to make that choice.
