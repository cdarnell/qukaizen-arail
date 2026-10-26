"""``arailctl auth`` and the shell helper that carries the bearer (S4 step 5; ARCHITECTURE.md C7, F3, F12)."""
from __future__ import annotations

import io
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from arail import config
from arail.portal import auth, auth_cli

pytestmark = pytest.mark.real_auth
REPO = Path(__file__).resolve().parents[1]
PASS = "test-passphrase-not-real"


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("ARAIL_PASSWORD", PASS)
    monkeypatch.setenv("ARAIL_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*argv):
    out = io.StringIO()
    rc = auth_cli.main(list(argv), out=out)
    return rc, out.getvalue()


def test_token_prints_id_and_path_never_the_value(lab):
    rc, out = run("token")
    assert rc == 0
    tok = auth.token_path().read_text().strip()
    assert tok not in out and auth.read_token_id() in out and str(auth.token_path()) in out
    assert stat.S_IMODE(os.stat(auth.token_path()).st_mode) == 0o600


def test_rotate_kills_the_old_token_on_the_next_request(lab):
    run("token")
    old = auth.token_path().read_text().strip()
    assert auth.check_bearer(f"Bearer {old}")
    rc, out = run("token", "--rotate")
    new = auth.token_path().read_text().strip()
    assert rc == 0 and new != old and new not in out and old not in out
    assert not auth.check_bearer(f"Bearer {old}") and auth.check_bearer(f"Bearer {new}")


def test_token_refuses_before_onboarding_and_writes_nothing(lab, monkeypatch):
    monkeypatch.setenv("ARAIL_PASSWORD", "")
    rc, out = run("token")
    assert rc == 3 and "welcome" in out.lower()
    assert not auth.token_path().exists()


def test_other_users_lab_exits_3_with_the_owner_message(lab, monkeypatch):
    auth.ensure_token()
    real = os.stat(config.DATA_DIR).st_uid
    monkeypatch.setattr(os, "getuid", lambda: real + 1)
    rc, out = run("token")
    assert rc == 3 and "belongs to" in out and "run as that user" in out


def test_unsafe_token_file_is_refused_with_the_chmod_hint(lab):
    auth.ensure_token()
    os.chmod(auth.token_path(), 0o644)
    rc, out = run("token")
    assert rc == 3 and "chmod 600" in out


def test_reset_end_to_end_changes_the_passphrase_and_ends_sessions(lab, monkeypatch):
    from arail.portal import app as portal_app
    monkeypatch.setattr(portal_app, "_patch_lab_conf_password", lambda p: None)
    monkeypatch.setattr(portal_app, "_write_code_server_password", lambda p: None)
    sid = auth.get_store().create()
    answers = iter(["a-brand-new-pass", "a-brand-new-pass"])
    monkeypatch.setattr(auth_cli, "_read_pass", lambda prompt: next(answers))
    rc, out = run("reset")
    assert rc == 0
    assert "ARAIL_PASSWORD=a-brand-new-pass" in (lab / ".env").read_text()
    assert "a-brand-new-pass" not in out
    assert not auth.get_store().touch(sid)


@pytest.mark.parametrize("first,second", [("short", "short"), ("a-brand-new-pass", "different-pass-1")])
def test_reset_refuses_bad_entries_and_changes_nothing(lab, monkeypatch, first, second):
    answers = iter([first, second])
    monkeypatch.setattr(auth_cli, "_read_pass", lambda prompt: next(answers))
    sid = auth.get_store().create()
    rc, _ = run("reset")
    assert rc == 3 and not (lab / ".env").exists() and auth.get_store().touch(sid)


def test_logout_all_revokes_every_session(lab):
    sids = [auth.get_store().create() for _ in range(3)]
    rc, _ = run("logout-all")
    assert rc == 0 and not any(auth.get_store().touch(s) for s in sids)


def test_usage_errors_exit_2(lab):
    assert run()[0] == 2
    assert run("nonsense")[0] == 2


# --- F3: the bearer never reaches argv ---------------------------------------------------------
def test_no_script_builds_a_bearer_header_outside_the_helper():
    hits = []
    for f in [REPO / "arailctl", *sorted((REPO / "scripts").rglob("*.sh"))]:
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if "Bearer " in line and not (f.name == "services.sh" and "svc_portal_curl" in "".join(
                    f.read_text().splitlines()[max(0, n - 12):n])):
                hits.append(f"{f.relative_to(REPO)}:{n}")
    assert not hits, hits


def test_svc_portal_curl_keeps_the_token_out_of_argv(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    tok = "arl1_" + "T" * 43
    (data / "portal.token").write_text(tok + "\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "curl"
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$REC_ARGV"\n'
                    'for a in "$@"; do case "$a" in @*) cat "${a#@}" > "$REC_HDR";; esac; done\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "REC_ARGV": str(tmp_path / "argv"), "REC_HDR": str(tmp_path / "hdr")}
    r = subprocess.run(["bash", "-c", f'source "{REPO}/scripts/lib/services.sh"; '
                        f'svc_portal_curl "{data}" -sf http://127.0.0.1:1/x'],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    argv = (tmp_path / "argv").read_text()
    assert tok not in argv and "Bearer" not in argv and re.search(r"^@/dev/fd/\d+$", argv, re.M)
    assert (tmp_path / "hdr").read_text().strip() == f"Authorization: Bearer {tok}"


def test_svc_portal_curl_returns_77_without_a_readable_token(tmp_path):
    r = subprocess.run(["bash", "-c", f'source "{REPO}/scripts/lib/services.sh"; '
                        f'svc_portal_curl "{tmp_path}" -sf http://127.0.0.1:1/x; echo rc=$?'],
                       capture_output=True, text=True)
    assert "rc=77" in r.stdout


def test_shell_files_parse():
    for f in ("arailctl", "scripts/status.sh", "scripts/lib/services.sh"):
        assert subprocess.run(["bash", "-n", str(REPO / f)]).returncode == 0


def test_after_reset_the_running_portal_stops_accepting_the_old_passphrase(lab, monkeypatch):
    """Review ASK (a): the portal is another process, with the old passphrase still in its environment."""
    from fastapi.testclient import TestClient

    from arail.portal import app as portal_app
    monkeypatch.setattr(portal_app, "_patch_lab_conf_password", lambda p: None)
    monkeypatch.setattr(portal_app, "_write_code_server_password", lambda p: None)
    portal_app._write_env_kv("ARAIL_PASSWORD", PASS)                  # what onboarding wrote to disk
    answers = iter(["a-brand-new-pass", "a-brand-new-pass"])
    monkeypatch.setattr(auth_cli, "_read_pass", lambda prompt: next(answers))
    assert run("reset")[0] == 0
    monkeypatch.setenv("ARAIL_PASSWORD", PASS)                        # the portal process never saw the change
    c = TestClient(portal_app.app, follow_redirects=False)
    assert c.post("/login", data={"passphrase": PASS}).status_code == 401
    assert c.post("/login", data={"passphrase": "a-brand-new-pass"}).status_code == 303


def test_reset_also_replaces_both_tokens_and_forgets_remembered_browsers(lab, monkeypatch):
    """QA-S4-4: reset is what an owner presses after 'I think someone got in'."""
    from arail.portal import app as portal_app
    monkeypatch.setattr(portal_app, "_patch_lab_conf_password", lambda p: None)
    monkeypatch.setattr(portal_app, "_write_code_server_password", lambda p: None)
    run("token")
    old_tok = auth.token_path().read_text().strip()
    old_shim = auth.shim_token_path().read_text().strip()
    dev = auth.get_devices().create()
    answers = iter(["a-brand-new-pass", "a-brand-new-pass"])
    monkeypatch.setattr(auth_cli, "_read_pass", lambda prompt: next(answers))
    rc, out = run("reset")
    assert rc == 0
    assert not auth.check_bearer(f"Bearer {old_tok}") and not auth.check_shim_bearer(f"Bearer {old_shim}")
    assert auth.check_bearer(f"Bearer {auth.token_path().read_text().strip()}")
    assert not auth.get_devices().touch(dev)
    assert old_tok not in out and str(auth.token_path()) in out and "new id" in out
