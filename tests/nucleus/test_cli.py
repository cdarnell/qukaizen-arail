"""arail.nucleus.cli — verb dispatch, tier gate, exit codes, no-traceback
contract (T-CLI-1..4)."""

from __future__ import annotations

import pytest

from arail.nucleus import cli


# ── T-CLI-1: tier gate ───────────────────────────────────────────────

def test_maximus_verb_refused_on_minimalist(monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    code = cli.main(["build", "linux-kernel"])
    assert code == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "maximus" in err
    assert "arailctl tier maximus" in err
    assert "Traceback" not in err


def test_minimalist_verb_not_refused_on_minimalist(monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    code = cli.main(["plan", "linux-kernel"])
    # Not yet implemented at this commit — internal-error stub, but
    # crucially NOT the tier refusal (exit 3).
    assert code != cli.EXIT_REFUSED
    assert "maximus" not in capsys.readouterr().err


def test_maximus_verb_allowed_on_maximus(monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "maximus")
    code = cli.main(["build", "linux-kernel"])
    assert code != cli.EXIT_REFUSED


# ── T-CLI-2: unknown verb ────────────────────────────────────────────

def test_unknown_verb_exits_usage(capsys):
    code = cli.main(["frobnicate"])
    assert code == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "unknown verb" in err
    assert "usage:" in err


def test_no_args_exits_usage(capsys):
    code = cli.main([])
    assert code == cli.EXIT_USAGE


def test_help_flag_exits_ok(capsys):
    code = cli.main(["--help"])
    assert code == cli.EXIT_OK
    assert "usage:" in capsys.readouterr().out


# ── T-CLI-3: no traceback without --debug ────────────────────────────

def test_internal_error_has_no_traceback_without_debug(monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "maximus")

    def _boom(rest):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(cli._IMPLEMENTED, "build", _boom)
    code = cli.main(["build", "x"])
    assert code == cli.EXIT_INTERNAL
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "kaboom" in err


def test_internal_error_reraises_with_debug(monkeypatch):
    monkeypatch.setenv("LAB_TIER", "maximus")

    def _boom(rest):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(cli._IMPLEMENTED, "build", _boom)
    with pytest.raises(RuntimeError, match="kaboom"):
        cli.main(["build", "x", "--debug"])


# ── T-CLI-4: publish ─────────────────────────────────────────────────

def test_publish_always_refused(monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "maximus")
    code = cli.main(["publish"])
    assert code == cli.EXIT_REFUSED
    assert "nucleus-sprint-3" in capsys.readouterr().err


def test_publish_refused_even_on_minimalist(monkeypatch, capsys):
    monkeypatch.setenv("LAB_TIER", "minimalist")
    code = cli.main(["publish"])
    assert code == cli.EXIT_REFUSED
