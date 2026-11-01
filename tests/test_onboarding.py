"""First-run onboarding flow — passphrase detection + /welcome endpoint.

Verifies:
  - _lab_password_set() recognizes real values, rejects placeholders
  - HTML routes redirect to /welcome when no password is set
  - API routes return 401 when no password is set
  - /api/welcome/setup writes the password to .env, lab.conf, and
    code-server config; refuses to overwrite an existing password
  - The middleware unblocks once the password is set
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Don't import app at module level — we need to control the env var
# before the middleware checks it on the first request.


@pytest.fixture
def fresh_lab(monkeypatch, tmp_path):
    """Run each test in a temp working dir with no ARAIL_PASSWORD.

    Overrides the autouse fixture from conftest.py so we exercise the
    no-password path. Also chdir's into a temp dir so .env / lab.conf
    writes don't pollute the real repo.
    """
    monkeypatch.delenv("ARAIL_PASSWORD", raising=False)
    monkeypatch.delenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    # The autouse _isolated_env_file fixture points ARAIL_ENV_FILE at
    # tmp_path/"portal.env"; these tests assert on tmp_path/".env", so
    # re-point the override at that name (chdir alone covers lab.conf).
    monkeypatch.setenv("ARAIL_ENV_FILE", str(tmp_path / ".env"))
    # Point HOME at the tmp dir so the code-server write doesn't touch
    # the real ~/.config.
    monkeypatch.setenv("HOME", str(tmp_path))
    # Override the autouse _isolated_agent_observability_data_root
    # fixture's "already seen" default (conftest.py) the same way
    # tests/test_world_first_impression.py does: this suite's own
    # test_dashboard_unblocks_after_onboarding is specifically about the
    # one-shot World-prompt marker being ABSENT after a fresh onboarding,
    # so it needs a guaranteed-never-touched path, not whatever ambient
    # default the global fixture picked for everyone else.
    from arail.portal import app as portal_app
    monkeypatch.setattr(
        portal_app, "_world_prompt_marker",
        lambda: tmp_path / ".world-prompt-seen-fresh-lab-override",
    )
    yield tmp_path


def _new_client():
    from arail.portal.app import app
    return TestClient(app)


def test_lab_password_set_recognizes_real_value(monkeypatch):
    from arail.portal.app import _lab_password_set
    monkeypatch.setenv("ARAIL_PASSWORD", "a-real-passphrase-32-chars-here-x")
    assert _lab_password_set() is True


def test_lab_password_set_rejects_empty(monkeypatch, tmp_path):
    from arail.portal.app import _lab_password_set
    monkeypatch.delenv("ARAIL_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env to fall back to
    assert _lab_password_set() is False


def test_lab_password_set_rejects_placeholder(monkeypatch, tmp_path):
    from arail.portal.app import _lab_password_set
    monkeypatch.setenv("ARAIL_PASSWORD", "__needs_setup__")
    monkeypatch.chdir(tmp_path)
    assert _lab_password_set() is False


def test_lab_password_set_falls_back_to_env_file(monkeypatch, tmp_path):
    """Env var empty but .env on disk has a real password — should
    detect it (and pull it into os.environ for downstream callers).

    Deliberately drops ARAIL_ENV_FILE to exercise the production
    cwd-relative fallback path (safe: cwd is a tmp dir here)."""
    from arail.portal.app import _lab_password_set
    monkeypatch.delenv("ARAIL_PASSWORD", raising=False)
    monkeypatch.delenv("ARAIL_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ARAIL_PASSWORD=hello-from-env-file\n")
    assert _lab_password_set() is True
    # Should also have populated os.environ.
    assert os.environ.get("ARAIL_PASSWORD") == "hello-from-env-file"


def test_html_route_redirects_to_welcome_when_unset(fresh_lab):
    client = _new_client()
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/welcome"


def test_api_route_returns_401_when_unset(fresh_lab):
    client = _new_client()
    r = client.get("/api/jobs/state")
    assert r.status_code == 401
    assert r.json().get("error") == "lab_not_onboarded"


def test_welcome_page_renders_when_unset(fresh_lab):
    client = _new_client()
    r = client.get("/welcome")
    assert r.status_code == 200
    assert "Welcome" in r.text
    assert "Passphrase" in r.text


def test_welcome_setup_writes_credentials(fresh_lab):
    client = _new_client()
    r = client.post("/api/welcome/setup", json={
        "passphrase": "a-real-passphrase-here",
        "confirm":    "a-real-passphrase-here",
        "lab_name":   "Test Lab",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    env_text = (fresh_lab / ".env").read_text()
    assert "ARAIL_PASSWORD=a-real-passphrase-here" in env_text
    assert "OPEN_NOTEBOOK_ENCRYPTION_KEY=a-real-passphrase-here" in env_text
    assert "LAB_NAME=Test Lab" in env_text

    lab_conf = (fresh_lab / "lab.conf").read_text()
    assert "IDE_PASSWORD=a-real-passphrase-here" in lab_conf

    cs_cfg = (fresh_lab / ".config" / "code-server" / "config.yaml").read_text()
    assert "password: a-real-passphrase-here" in cs_cfg


def test_welcome_setup_rejects_short_passphrase(fresh_lab):
    client = _new_client()
    r = client.post("/api/welcome/setup", json={
        "passphrase": "short",
        "confirm":    "short",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "8 characters" in body["error"]


def test_welcome_setup_rejects_mismatched_confirm(fresh_lab):
    client = _new_client()
    r = client.post("/api/welcome/setup", json={
        "passphrase": "a-real-passphrase-here",
        "confirm":    "different-passphrase-here",
    })
    body = r.json()
    assert body["ok"] is False
    assert "match" in body["error"]


def test_welcome_setup_refuses_overwrite_when_already_onboarded(monkeypatch, tmp_path):
    """Once a real password exists, /api/welcome/setup must 409 to
    prevent an unauthenticated overwrite."""
    monkeypatch.setenv("ARAIL_PASSWORD", "already-set-passphrase")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    client = _new_client()
    r = client.post("/api/welcome/setup", json={
        "passphrase": "trying-to-overwrite-here",
        "confirm":    "trying-to-overwrite-here",
    })
    assert r.status_code == 409
    body = r.json()
    assert body["ok"] is False


def test_dashboard_unblocks_after_onboarding(fresh_lab):
    """End-to-end: blocked → onboard → dashboard reachable.

    Post-onboarding, with no World mounted and the one-shot World-prompt
    marker unset, the FIRST dashboard request now redirects to the World
    step (Door 2, C3/C4) rather than rendering the dashboard directly. A
    second request (marker now written) reaches the dashboard.
    """
    client = _new_client()
    # First request: blocked.
    assert client.get("/", follow_redirects=False).status_code == 302
    # Onboard.
    r = client.post("/api/welcome/setup", json={
        "passphrase": "shiny-new-passphrase",
        "confirm":    "shiny-new-passphrase",
    })
    assert r.json()["ok"] is True
    # Onboarded, no World mounted yet → the one-shot World-prompt nudge fires.
    first = client.get("/", follow_redirects=False)
    assert first.status_code == 302
    assert first.headers["location"] == "/welcome?step=world"
    # Dashboard now reachable on the next request (marker already written).
    assert client.get("/", follow_redirects=False).status_code == 200


# ── Welcome mode step (step 2 of 3) ─────────────────────────────────────────


def test_airgap_toggle_blocked_before_onboarding(fresh_lab):
    """The mode step's writer is NOT allowlisted — the gate must 401 it."""
    client = _new_client()
    r = client.post("/api/airgap/toggle", json={"target": "hybrid"},
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 401
    assert r.json().get("error") == "lab_not_onboarded"


def test_welcome_then_mode_toggle_sequence(fresh_lab, monkeypatch):
    """The welcome flow's exact call order: passphrase save unlocks the
    gate, then the mode step's toggle POST persists LAB_MODE."""
    import arail.portal.app as app_mod
    env_path = fresh_lab / ".env"
    audit_path = fresh_lab / "airgap_audit.jsonl"
    monkeypatch.setattr(app_mod, "_TOGGLE_ENV_PATH", env_path)
    monkeypatch.setattr(app_mod, "_TOGGLE_AUDIT_PATH", audit_path)
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    monkeypatch.setenv("LAB_MODE", "airgapped")

    client = _new_client()
    r = client.post("/api/welcome/setup", json={
        "passphrase": "shiny-new-passphrase",
        "confirm":    "shiny-new-passphrase",
    })
    assert r.json()["ok"] is True

    r = client.post("/api/airgap/toggle", json={"target": "hybrid"},
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 200, r.text
    assert r.json()["lab_mode"] == "hybrid"
    assert "LAB_MODE=hybrid" in env_path.read_text()
    # The first-run choice lands in the audit log.
    assert audit_path.exists()

    # Choosing airgapped (the default) also round-trips cleanly.
    r = client.post("/api/airgap/toggle", json={"target": "airgapped"},
                    headers={"Origin": "http://testserver"})
    assert r.status_code == 200
    assert "LAB_MODE=airgapped" in env_path.read_text()


def test_welcome_page_contains_mode_step_copy(fresh_lab):
    """Template-drift guard: the 3-step flow and its education copy exist."""
    client = _new_client()
    html = client.get("/welcome").text
    assert "Step 1 of 3" in html
    assert "Airgapped" in html
    assert "Hybrid" in html
    # The crucial airgapped education: self-provided research material +
    # in-box World terms.
    assert "material you provide" in html
    assert "ship inside ARAIL itself" in html
    # Mode writes go through the canonical gated writer.
    assert "/api/airgap/toggle" in html
