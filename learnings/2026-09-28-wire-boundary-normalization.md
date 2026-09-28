# Enforce wire-format rules at the request boundary, not in the value resolver

**When** a wire-format rule exists ("Ollama keep_alive must be a JSON number
when numeric — a bare string `"-1"` 400s as a unitless Go duration"), **do**
apply the normalization where the request body is built (`complete()` /
`stream_complete()`), **instead of** only inside the method that resolves the
value (`_keep_alive`), **because** resolver methods get forked and overridden:
this exact bug shipped twice — once when the pin value itself was the string
`"-1"` (every /api/chat call 400'd; the dashboard painted "unhealthy" over a
healthy lab; tests had encoded the implementation, not what the server
accepts), and again when a sprint branch's fork of `_keep_alive` drifted from
the fixed version. A guard at the body-construction site is on the path of
every producer — env override, pin, subclass, `__new__`-built instance,
future fork — so the class of bug dies rather than the instance.

Regression shape that keeps it dead: monkeypatch the resolver to return the
known-bad value and assert on the **outgoing body** (see
`tests/router/test_keep_alive.py::test_wire_guard_normalizes_a_drifted_keep_alive_override`).
