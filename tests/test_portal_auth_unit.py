"""Unit tests for arail.portal.auth (S4 step 1): token file, passphrase compare, sessions.

ARCHITECTURE.md C1; failure modes F21 (write storm), F22 (corrupt/tampered files), F26 (timing).
"""
from __future__ import annotations

import ast
import json
import os
import stat
import time
from pathlib import Path

import pytest

from arail import config
from arail.portal import auth

pytestmark = pytest.mark.real_auth


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", d)
    monkeypatch.setattr(auth, "_stores", {})
    return d


def _tok(data_dir: Path) -> str:
    return (data_dir / "portal.token").read_text().strip()


def test_ensure_token_mints_0600_in_0700_dir_and_is_idempotent(data_dir):
    auth.ensure_token()
    p = data_dir / "portal.token"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(data_dir).st_mode) == 0o700
    t = _tok(data_dir)
    assert t.startswith("arl1_") and len(t) == 48
    auth.ensure_token()
    assert _tok(data_dir) == t


def test_check_bearer_accepts_only_the_exact_token(data_dir):
    auth.ensure_token()
    t = _tok(data_dir)
    assert auth.check_bearer(f"Bearer {t}")
    assert not auth.check_bearer(f"bearer {t}")
    assert not auth.check_bearer(t)
    assert not auth.check_bearer(f"Bearer {t}x")
    assert not auth.check_bearer("Bearer arl1_" + "A" * 43)
    assert not auth.check_bearer("Bearer bhc1_" + "A" * 43)
    assert not auth.check_bearer("")


def test_rotation_takes_effect_without_restart(data_dir):
    auth.ensure_token()
    old = _tok(data_dir)
    assert auth.check_bearer(f"Bearer {old}")
    new_id = auth.rotate_token()
    new = _tok(data_dir)
    assert new != old and len(new_id) == 8
    assert not auth.check_bearer(f"Bearer {old}")
    assert auth.check_bearer(f"Bearer {new}")


def test_rotate_returns_id_not_token(data_dir):
    tid = auth.rotate_token()
    assert tid == auth.read_token_id()
    assert _tok(data_dir) not in tid


def test_wide_mode_token_file_refuses_bearer(data_dir, caplog):
    auth.ensure_token()
    t = _tok(data_dir)
    os.chmod(data_dir / "portal.token", 0o644)
    assert not auth.check_bearer(f"Bearer {t}")
    assert auth.token_file_state() == "unsafe"
    assert "chmod 600" in caplog.text and t not in caplog.text
    os.chmod(data_dir / "portal.token", 0o600)
    assert auth.check_bearer(f"Bearer {t}")


def test_symlinked_token_file_refuses_bearer(data_dir, tmp_path):
    auth.ensure_token()
    real = tmp_path / "elsewhere.token"
    (data_dir / "portal.token").rename(real)
    (data_dir / "portal.token").symlink_to(real)
    assert not auth.check_bearer(f"Bearer {real.read_text().strip()}")
    with pytest.raises(OSError):
        auth.rotate_token()


def test_foreign_owner_token_file_refuses_bearer(data_dir, monkeypatch):
    auth.ensure_token()
    t = _tok(data_dir)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(data_dir / "portal.token").st_uid + 1)
    assert not auth.check_bearer(f"Bearer {t}")


def test_two_data_dirs_have_two_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "a")
    auth.ensure_token()
    ta = (tmp_path / "a" / "portal.token").read_text().strip()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "b")
    auth.ensure_token()
    assert not auth.check_bearer(f"Bearer {ta}")


def test_check_password(monkeypatch):
    monkeypatch.setenv("ARAIL_PASSWORD", "correct horse battery")
    assert auth.check_password("correct horse battery")
    assert not auth.check_password("correct horse batterx")
    assert not auth.check_password("")
    for placeholder in ("", "change-me", "__needs_setup__"):
        monkeypatch.setenv("ARAIL_PASSWORD", placeholder)
        assert not auth.check_password(placeholder)


def test_session_lifecycle(data_dir):
    s = auth.get_store()
    sid = s.create()
    assert s.touch(sid)
    assert not s.touch("nope")
    assert not s.touch("")
    s.revoke(sid)
    assert not s.touch(sid)


def test_session_file_stores_only_hash_and_is_0600(data_dir):
    sid = auth.get_store().create()
    p = data_dir / "portal_sessions.json"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert sid not in p.read_text()


def test_session_survives_restart_and_idle_expiry(data_dir):
    sid = auth.get_store().create()
    again = auth.SessionStore(data_dir / "portal_sessions.json")
    assert again.touch(sid)
    again.force_expire(sid)
    assert not again.touch(sid)


def test_session_absolute_expiry(data_dir, monkeypatch):
    s = auth.get_store()
    sid = s.create()
    row = next(iter(s._rows.values()))
    row["created"] -= auth.ABSOLUTE_EXPIRY_S + 5
    assert not s.touch(sid)


def test_session_cap_evicts_oldest(data_dir):
    s = auth.get_store()
    first = s.create()
    time.sleep(0.01)
    sids = [s.create() for _ in range(auth.MAX_SESSIONS)]
    assert len(s._rows) == auth.MAX_SESSIONS
    assert not s.touch(first)
    assert s.touch(sids[-1])


def test_thousand_touches_cause_at_most_one_write(data_dir):
    """F21: a last_seen write per request would hammer the SSD (SSE polls make thousands)."""
    s = auth.get_store()
    sid = s.create()
    before = s.writes
    for _ in range(1000):
        assert s.touch(sid)
    assert s.writes - before <= 1


def test_corrupt_sessions_file_starts_empty_without_crashing(data_dir, caplog):
    data_dir.mkdir(parents=True)
    p = data_dir / "portal_sessions.json"
    p.write_text("{not json")
    os.chmod(p, 0o600)
    s = auth.SessionStore(p)
    assert s._rows == {}
    assert "ignored" in caplog.text
    assert s.create()


def test_wide_mode_sessions_file_is_ignored(data_dir):
    sid = auth.get_store().create()
    p = data_dir / "portal_sessions.json"
    os.chmod(p, 0o644)
    assert not auth.SessionStore(p).touch(sid)


def test_symlinked_sessions_file_is_ignored(data_dir, tmp_path):
    sid = auth.get_store().create()
    p = data_dir / "portal_sessions.json"
    real = tmp_path / "real.json"
    p.rename(real)
    p.symlink_to(real)
    assert not auth.SessionStore(p).touch(sid)


def test_limiter_backoff_and_reset():
    lim = auth.LoginLimiter()
    for _ in range(4):
        lim.failure()
    assert lim.wait_s() == 0
    lim.failure()
    assert 0 < lim.wait_s() <= 10
    for _ in range(20):
        lim.failure()
    assert lim.wait_s() <= auth.LoginLimiter.CAP_S
    lim.success()
    assert lim.wait_s() == 0


@pytest.mark.parametrize("bad", ["//evil.com", "/\\evil.com", "https://evil.com", "/%2F%2Fevil.com",
                                 "/a\r\nSet-Cookie: x=1", "", None])
def test_safe_next_rejects_open_redirects(bad):
    assert auth.safe_next(bad) == "/"


def test_safe_next_keeps_local_paths():
    assert auth.safe_next("/research?tab=2") == "/research?tab=2"


def test_secret_comparison_is_constant_time_only():
    """F26: no ==/!= anywhere in the credential-checking functions; compare_digest does the work."""
    tree = ast.parse(Path(auth.__file__).read_text())
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("check_bearer", "check_password"):
        cmps = [c for c in ast.walk(fns[name]) if isinstance(c, ast.Compare)
                and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in c.ops)]
        assert not cmps, f"{name} compares with ==/!="
        calls = [c for c in ast.walk(fns[name]) if isinstance(c, ast.Call)
                 and getattr(c.func, "attr", "") == "compare_digest"]
        assert calls, f"{name} does not use hmac.compare_digest"


def test_exempt_set_is_exactly_the_specified_list():
    assert set(auth.EXEMPT_EXACT) == {"/login", "/favicon.ico", "/health", "/healthz",
                                      "/api/system/health", "/api/instance"}
    assert auth.is_exempt("/static/x.css", "GET") and not auth.is_exempt("/static/x.css", "POST")
    assert not auth.is_exempt("/api/system/health/stream", "GET")
    assert not auth.is_exempt("/api/instances", "GET")
    assert not auth.is_exempt("/metrics", "GET")


# --- review BLOCK: a revoke from a second process must reach the running portal -----------------
def test_revoke_all_in_a_second_store_ends_sessions_in_the_first(data_dir):
    """The portal holds its store in memory; `arailctl auth logout-all` is a different process."""
    portal = auth.SessionStore(data_dir / "portal_sessions.json")
    sid = portal.create()
    assert portal.touch(sid)
    cli = auth.SessionStore(data_dir / "portal_sessions.json")
    cli.revoke_all()
    assert not portal.touch(sid)


def test_single_revoke_in_a_second_store_reaches_the_first_and_spares_the_rest(data_dir):
    portal = auth.SessionStore(data_dir / "portal_sessions.json")
    keep, drop = portal.create(), portal.create()
    other = auth.SessionStore(data_dir / "portal_sessions.json")
    other.revoke(drop)
    assert not portal.touch(drop) and portal.touch(keep)


def test_the_hourly_save_does_not_resurrect_revoked_sessions(data_dir):
    portal = auth.SessionStore(data_dir / "portal_sessions.json")
    keep, drop = portal.create(), portal.create()
    auth.SessionStore(data_dir / "portal_sessions.json").revoke(drop)
    portal._persisted_seen[portal._key(keep)] = 0.0           # the hourly write is now due
    assert portal.touch(keep) and not portal.touch(drop)      # touch(keep) saves
    assert drop not in (data_dir / "portal_sessions.json").read_text()
    fresh = auth.SessionStore(data_dir / "portal_sessions.json")
    assert fresh.touch(keep) and not fresh.touch(drop)


def test_a_session_created_in_a_second_store_is_adopted(data_dir):
    portal = auth.SessionStore(data_dir / "portal_sessions.json")
    portal.create()
    sid2 = auth.SessionStore(data_dir / "portal_sessions.json").create()
    assert portal.touch(sid2)


def test_a_deleted_sessions_file_ends_every_session(data_dir):
    portal = auth.SessionStore(data_dir / "portal_sessions.json")
    sid = portal.create()
    (data_dir / "portal_sessions.json").unlink()
    assert not portal.touch(sid)


def test_unchanged_file_is_not_reread_per_request(data_dir):
    s = auth.SessionStore(data_dir / "portal_sessions.json")
    sid = s.create()
    before = s.reloads
    for _ in range(200):
        s.touch(sid)
    assert s.reloads == before


# --- QA-S4-1: open streams end when their credential is revoked ----------------------------------
def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_guard_stream_ends_with_one_auth_event_and_nothing_after(monkeypatch):
    import asyncio
    monkeypatch.setattr(auth, "STREAM_TICK_S", 0.05)
    valid = {"ok": True}
    closed = {"v": False}

    async def source():
        try:
            yield b"data: 1\n\n"
            while True:
                await asyncio.sleep(10)          # an idle activity feed
                yield b"data: never\n\n"
        finally:
            closed["v"] = True

    async def go():
        out = []
        async for chunk in auth.guard_stream(source(), lambda: valid["ok"]):
            out.append(chunk)
            if len(out) == 1:
                valid["ok"] = False              # revoked while the source is idle
        return out

    out = _run(asyncio.wait_for(go(), 3))
    assert out == [b"data: 1\n\n", auth.AUTH_EVENT]
    assert closed["v"]


def test_guard_stream_passes_events_through_while_valid_and_ends_when_the_source_ends(monkeypatch):
    monkeypatch.setattr(auth, "STREAM_TICK_S", 0.05)

    async def source():
        for i in range(3):
            yield f"data: {i}\n\n".encode()

    async def go():
        return [c async for c in auth.guard_stream(source(), lambda: True)]

    assert _run(go()) == [b"data: 0\n\n", b"data: 1\n\n", b"data: 2\n\n"]


def test_a_revoked_session_stops_a_stream_even_if_events_keep_coming(monkeypatch):
    import asyncio
    monkeypatch.setattr(auth, "STREAM_TICK_S", 0.05)
    valid = {"ok": True}

    async def chatty():
        while True:
            yield b"data: x\n\n"
            await asyncio.sleep(0.01)

    async def go():
        out = []
        async for chunk in auth.guard_stream(chatty(), lambda: valid["ok"]):
            out.append(chunk)
            if len(out) == 3:
                valid["ok"] = False
        return out

    out = _run(asyncio.wait_for(go(), 3))
    assert out[-1] == auth.AUTH_EVENT and out.count(auth.AUTH_EVENT) == 1
