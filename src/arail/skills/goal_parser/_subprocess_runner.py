"""Subprocess worker for the goal parser's LLM call.

This script is invoked by ``GoalParser.parse()`` as
``python -m arail.skills.goal_parser._subprocess_runner`` so the LLM
inference happens in an isolated process. If the Metal allocator
throws a C++ OOM (the failure mode that has historically nuked the
whole lab), only this subprocess dies — the parent observes a
non-zero exit code, swallows it, and falls back to the heuristic
parser.

Protocol
--------
* **Input** (stdin, JSON): ``{"prompt": str, "max_tokens": int,
  "temperature": float, "trace_id": str, "agent_id": str|null,
  "brain": str|null, "effort": str|null}`` — the last four are the
  attribution round-trip (ARCHITECTURE.md contract #9, sprint
  2026-09-20-buddy-front-and-center): the child sets its own
  attribution context from them so its *own* ``cost_tracker.track()``
  call is billed correctly, but it never writes an agent-trace record
  itself — the parent is the sole trace author, so there is exactly
  one writer of ``agent_traces.jsonl`` per process and no interleaved
  appends.
* **Output** (stdout, JSON): ``{"ok": true, "text": "...", "model":
  str|null, "backend": str|null, "tokens_used": int|null}`` on
  success, ``{"ok": false, "error": "...", "error_class": "..."}`` on a
  recoverable Python-level failure. ``error`` is a human-readable
  message for logs and may contain arbitrary text (including a
  request fragment from a backend exception); ``error_class`` is
  always ``type(exc).__name__``-shaped (or a synthetic name for a
  non-exception failure, e.g. ``MissingPrompt``) and is the ONLY one
  of the two the parent is allowed to put into a trace record — QA F2
  (TEST_REPORT.md): the trace schema documents error_class as a class
  name, and the parent enforces this with its own allow-list
  regardless of what this child sends.
* **Crash** (exit code != 0): the parent treats this as
  "subprocess died, fall back."

Keep this file dependency-light: it imports the ModelRouter, which
already lazily picks the right backend.
"""

from __future__ import annotations

import json
import sys
import traceback


def _main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({
            "ok": False, "error": f"bad input: {e}",
            "error_class": type(e).__name__,
        }))
        return 0

    prompt = request.get("prompt", "")
    if not prompt:
        sys.stdout.write(json.dumps({
            "ok": False, "error": "missing prompt",
            "error_class": "MissingPrompt",
        }))
        return 0
    max_tokens = int(request.get("max_tokens", 800))
    temperature = float(request.get("temperature", 0.5))

    try:
        # Lazy import — avoids paying the MLX load cost when callers
        # only want to inspect the protocol or run the parent's tests.
        from arail import agent_context
        from arail.router import ModelRouter
        router = ModelRouter()
        with agent_context.from_subprocess_payload(
            request, default_label="goal-parser"
        ):
            resp = router.complete(prompt, max_tokens=max_tokens, temperature=temperature)
        sys.stdout.write(json.dumps({
            "ok": True, "text": resp.text,
            "model": getattr(resp, "model", None),
            "backend": getattr(resp, "backend", None),
            "tokens_used": getattr(resp, "tokens_used", None),
        }))
        return 0
    except MemoryError as e:
        # The mlx_guard pre-check throws MetalOutOfMemory, which is a
        # RuntimeError — caught below. MemoryError is the Python-side
        # symptom for some allocator paths; surface it cleanly so the
        # parent can attribute the fallback correctly.
        sys.stdout.write(json.dumps({
            "ok": False, "error": f"OOM: {e}",
            "error_class": type(e).__name__,
        }))
        return 0
    except Exception as e:
        # Any other Python-level error: surface it so the parent can
        # log a meaningful message before falling back. "error" carries
        # the full message for a human reading logs; "error_class" is
        # the only field the parent may put into a trace record.
        sys.stdout.write(json.dumps({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "error_class": type(e).__name__,
            "trace": traceback.format_exc()[-500:],
        }))
        return 0


if __name__ == "__main__":
    sys.exit(_main())
