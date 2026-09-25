"""Unit tests for arail.redact — the flight recorder's only body-writing
path. Each shape pattern, the known-value pass, ordering (redact-then-
truncate), and capture_body's fail-closed behaviour (F11).

QA-BLIND-1 (planted key-shaped string, not in secrets.env) is authored
independently by QA from ARCHITECTURE.md's contract, not from this file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from arail import config, redact


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    redact._reset_cache_for_tests()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv("ARAIL_PASSWORD", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ARAIL_SECRETS_FILE", raising=False)
    yield
    redact._reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Shape pass — one test per pattern
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("planted", [
    "sk-abcdefghijklmnopqrstuvwx",
    "hf_ABCDEFGHIJKLMNOPQRSTuvwx",
    "nvapi-abcdefghijklmnopqrstuvwx",
    "ghp_abcdefghijklmnopqrstuvwx",
    "gho_abcdefghijklmnopqrstuvwx",
    "github_pat_abcdefghijklmnopqrstuvwx",
    "AKIAABCDEFGHIJKLMNOP",
    "Bearer abcdefghijklmnopqrstuvwxyz01234",
    "api_key: abcdefghijklmnop",
    "token=abcdefghijklmnop",
    "secret: abcdefghijklmnop",
    "password=abcdefghijklmnop",
    "passphrase: abcdefghijklmnop",
])
def test_shape_pass_redacts_each_pattern(planted):
    text = f"here is my key: {planted} — please keep it safe"
    out, n = redact.redact(text)
    assert planted not in out
    assert redact.REDACTED in out
    assert n >= 1


def test_shape_pass_does_not_false_positive_on_ordinary_text():
    text = "The quick brown fox jumps over the lazy dog. No secrets here."
    out, n = redact.redact(text)
    assert out == text
    assert n == 0


# ---------------------------------------------------------------------------
# Known-value pass
# ---------------------------------------------------------------------------

def test_known_value_from_secrets_env_is_redacted(tmp_path):
    (tmp_path / "secrets.env").write_text("CLAUDE_API_KEY=my-super-secret-value-123\n")
    text = "using key my-super-secret-value-123 for this call"
    out, n = redact.redact(text)
    assert "my-super-secret-value-123" not in out
    assert n >= 1


def test_known_value_below_min_length_is_not_redacted(tmp_path):
    (tmp_path / "secrets.env").write_text("SHORT=abc\n")
    text = "the value abc appears here"
    out, n = redact.redact(text)
    assert out == text  # too short to treat as a secret (avoids false positives)


def test_arail_password_env_is_redacted(monkeypatch):
    monkeypatch.setenv("ARAIL_PASSWORD", "correct-horse-battery-staple")
    text = "the operator's password is correct-horse-battery-staple"
    out, n = redact.redact(text)
    assert "correct-horse-battery-staple" not in out
    assert n >= 1


def test_open_notebook_encryption_key_env_is_redacted(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "0123456789abcdef0123456789abcdef")
    text = "key: 0123456789abcdef0123456789abcdef"
    out, n = redact.redact(text)
    assert "0123456789abcdef0123456789abcdef" not in out


def test_secrets_cache_refreshes_on_mtime_change(tmp_path):
    path = tmp_path / "secrets.env"
    path.write_text("K1=first-secret-value\n")
    out1, _ = redact.redact("contains first-secret-value here")
    assert "first-secret-value" not in out1

    import time
    time.sleep(0.01)
    path.write_text("K1=second-secret-value\n")
    out2, _ = redact.redact("contains second-secret-value here")
    assert "second-secret-value" not in out2


# ---------------------------------------------------------------------------
# Ordering: redact-then-truncate (a key spanning the truncation boundary is
# still redacted).
# ---------------------------------------------------------------------------

def test_capture_body_redacts_before_truncating():
    padding = "x" * 1990
    key = "sk-abcdefghijklmnopqrstuvwxyz012345"
    prompt = padding + key  # the key starts right at/after the 2000-char cap
    body = redact.capture_body(prompt, "")
    assert body is not None
    assert "sk-" not in body["prompt"]
    assert key not in body["prompt"]
    assert body["redactions"] >= 1


def test_capture_body_caps_prompt_and_response_length():
    body = redact.capture_body("p" * 5000, "r" * 5000)
    assert body is not None
    assert len(body["prompt"]) == 2000
    assert len(body["response"]) == 1000
    assert body["truncated"] is True


def test_capture_body_not_truncated_under_cap():
    body = redact.capture_body("short prompt", "short response")
    assert body["truncated"] is False


# ---------------------------------------------------------------------------
# F11 — fail-closed on bodies
# ---------------------------------------------------------------------------

def test_capture_body_returns_none_on_redactor_failure(monkeypatch):
    def _boom(text):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(redact, "_redact_strict", _boom)
    assert redact.capture_body("hi", "there") is None


def test_capture_body_returns_none_when_known_values_pass_raises(monkeypatch):
    """REVIEW.md B1's exact acceptance test: capture_body must fail closed
    when the known-value pass itself raises, not just when capture_body's
    own outer wrapper does."""
    def _boom():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")

    monkeypatch.setattr(redact, "_known_values", _boom)
    assert redact.capture_body("a prompt containing supersecretvalue123", "resp") is None


def test_redact_strict_propagates_known_values_failure(monkeypatch):
    """redact() (the lenient, general-purpose function) must stay
    unaffected -- only _redact_strict (capture_body's own path) propagates."""
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(redact, "_known_values", _boom)
    with pytest.raises(RuntimeError):
        redact._redact_strict("some text")
    # The lenient public redact() still degrades gracefully.
    out, n = redact.redact("some text")
    assert out == "some text"


def test_capture_body_handles_none_inputs():
    body = redact.capture_body(None, None)
    assert body == {"prompt": "", "response": "", "truncated": False, "redactions": 0}


def test_capture_body_handles_non_string_inputs():
    body = redact.capture_body(12345, ["not", "a", "string"])
    assert body is not None
    assert body["prompt"] == ""
    assert body["response"] == ""


def test_b1_non_utf8_secrets_env_still_redacts_other_values(tmp_path):
    """REVIEW.md B1's reproduction scenario, now fixed: a secrets.env with
    one stray non-UTF-8 byte inside a key's value must not silently
    disable the whole known-value pass. errors="replace" keeps the file
    parseable; the OTHER lines' values still redact."""
    path = tmp_path / "secrets.env"
    # A clean key plus a line with a raw invalid UTF-8 byte inside the
    # value (a stray \xff, e.g. from a terminal paste or non-Python writer).
    path.write_bytes(
        b"CLEAN_KEY=supersecretvalue123\n"
        b"MANGLED_KEY=abc\xffdef01234567\n"
    )
    redact._reset_cache_for_tests()
    body = redact.capture_body("here is supersecretvalue123 in a prompt", "resp")
    assert body is not None
    assert "supersecretvalue123" not in body["prompt"]
    assert body["redactions"] >= 1


def test_b1_non_utf8_secrets_env_does_not_raise():
    """The file itself must never be the thing that turns a capture into
    a raised exception -- confirmed via the real parser, not a mock."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "secrets.env"
        path.write_bytes(b"KEY=bad\xffvalue0123456789\n")
        values = redact._parse_secrets_env(path)
        assert isinstance(values, list)  # never raises, always a list


# ---------------------------------------------------------------------------
# R1 (re-review of the B1 fix) — an *existing-but-unreadable* secrets.env
# must not collapse into "no known values" the same way an absent or
# genuinely empty one legitimately does. "Can't read it" and "found
# nothing" must stay distinguishable, or capture_body silently returns a
# shape-pass-only, under-redacted body with redactions: 0 -- identical to
# the normal result for a clean prompt.
# ---------------------------------------------------------------------------

def test_r1_existing_but_unreadable_secrets_env_fails_capture_closed(tmp_path):
    """The reviewer's own repro: secrets.env present, chmod 000, a prompt
    containing the value that file would have redacted. Before the fix
    this returned a body with the secret still in it and redactions: 0
    -- indistinguishable from a clean prompt. Skips cleanly under root,
    where chmod 000 does not actually block reads."""
    if os.geteuid() == 0:
        pytest.skip("running as root -- chmod 000 is still readable")
    path = tmp_path / "secrets.env"
    path.write_text("API_KEY=supersecretvalue123\n")
    path.chmod(0o000)
    try:
        redact._reset_cache_for_tests()
        body = redact.capture_body(
            "a prompt containing supersecretvalue123 in it", "resp"
        )
        assert body is None
    finally:
        path.chmod(0o600)  # restore so tmp_path's own cleanup can remove it


def test_r1_missing_secrets_env_still_captures():
    """The state R1 must NOT be confused with "unreadable": a genuinely
    absent secrets.env is not an error. capture_body still runs the shape
    pass and returns a body."""
    redact._reset_cache_for_tests()
    body = redact.capture_body("a prompt with sk-shapepassonlyvalue012345", "resp")
    assert body is not None
    assert "sk-shapepassonlyvalue012345" not in body["prompt"]


def test_r1_mangled_but_readable_secrets_env_still_redacts_other_lines(tmp_path):
    """Guard against R1's fix over-tightening: B1's other half (a
    *readable* file with one decode-noise line) must keep redacting every
    other line's value -- this is the same scenario as
    test_b1_non_utf8_secrets_env_still_redacts_other_values, re-asserted
    here specifically alongside R1's new unreadable-file test so the two
    cases (mangled-but-readable vs. genuinely unreadable) are pinned
    side by side."""
    path = tmp_path / "secrets.env"
    path.write_bytes(
        b"CLEAN_KEY=supersecretvalue123\n"
        b"MANGLED_KEY=abc\xffdef01234567\n"
    )
    redact._reset_cache_for_tests()
    body = redact.capture_body("here is supersecretvalue123 in a prompt", "resp")
    assert body is not None
    assert "supersecretvalue123" not in body["prompt"]


def test_parse_secrets_env_raises_secrets_unreadable_on_permission_error(tmp_path):
    """Direct unit test of the new signal, independent of capture_body's
    own fail-closed wrapper."""
    if os.geteuid() == 0:
        pytest.skip("running as root -- chmod 000 is still readable")
    path = tmp_path / "secrets.env"
    path.write_text("API_KEY=supersecretvalue123\n")
    path.chmod(0o000)
    try:
        with pytest.raises(redact._SecretsUnreadable):
            redact._parse_secrets_env(path)
    finally:
        path.chmod(0o600)


def test_redact_lenient_still_degrades_gracefully_on_unreadable_secrets_env(tmp_path):
    """redact() (the lenient public function, used for display text) must
    keep swallowing the new _SecretsUnreadable exactly like any other
    _known_values() failure -- only capture_body's strict path (via
    _redact_strict) fails closed."""
    if os.geteuid() == 0:
        pytest.skip("running as root -- chmod 000 is still readable")
    path = tmp_path / "secrets.env"
    path.write_text("API_KEY=supersecretvalue123\n")
    path.chmod(0o000)
    try:
        redact._reset_cache_for_tests()
        out, n = redact.redact("plain text, no secret shape")
        assert out == "plain text, no secret shape"
        assert n == 0
    finally:
        path.chmod(0o600)


# ---------------------------------------------------------------------------
# The planted-key QA scenario, done once here too (belt-and-suspenders; the
# authoritative version is QA's own blind test written from the contract).
# ---------------------------------------------------------------------------

def test_planted_key_not_in_secrets_env_still_caught():
    planted = "sk-plantedkeynotinanysecretsenvfile01234"
    body = redact.capture_body(f"prompt containing {planted}", "response")
    assert body is not None
    assert planted not in body["prompt"]
    assert planted not in body["response"]
    assert body["redactions"] >= 1
