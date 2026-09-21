"""Unit tests for arail.redact — the flight recorder's only body-writing
path. Each shape pattern, the known-value pass, ordering (redact-then-
truncate), and capture_body's fail-closed behaviour (F11).

QA-BLIND-1 (planted key-shaped string, not in secrets.env) is authored
independently by QA from ARCHITECTURE.md's contract, not from this file.
"""

from __future__ import annotations

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

    monkeypatch.setattr(redact, "redact", _boom)
    assert redact.capture_body("hi", "there") is None


def test_capture_body_handles_none_inputs():
    body = redact.capture_body(None, None)
    assert body == {"prompt": "", "response": "", "truncated": False, "redactions": 0}


def test_capture_body_handles_non_string_inputs():
    body = redact.capture_body(12345, ["not", "a", "string"])
    assert body is not None
    assert body["prompt"] == ""
    assert body["response"] == ""


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
