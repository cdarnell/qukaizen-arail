"""A6 - `./arailctl buddy link | unlink | status`.

ARCHITECTURE.md §5.2 and §5.12: one capability credential per lab (`bhc1_` + 32
random bytes), ARAIL keeps only its sha256 and Buddy keeps the plaintext in a 0600
file; the link records the operator's consent to api.telegram.org; every placement
mistake is refused (exit 3) before anything is written. F35: a symlink or a wrong
owner on a link target is a refusal.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

import pytest

from arail import config
from arail.agents.consent import ConsentStore
from arail.buddy_host import CONTRACT_SHA256, link

REPO = pathlib.Path(__file__).resolve().parents[1]
CRED_RE = re.compile(r"^bhc1_[A-Za-z0-9_-]{43}$")


class _Stdin(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A hermetic lab: its own data dir, consent store, and a 0700 Buddy home."""
    data = tmp_path / "lab-data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.delenv("ARAIL_INSTANCE", raising=False)
    monkeypatch.setenv("BIND_ADDR", "127.0.0.1")
    monkeypatch.setenv("PORTAL_PORT", "8080")
    monkeypatch.setenv("ARAIL_EDGE_PORT", "8089")              # S5: link refuses with the edge app off; no listener is started by these tests
    home = tmp_path / "buddy-home"
    home.mkdir(mode=0o700)
    os.chmod(home, 0o700)
    store = ConsentStore(tmp_path / "consent")

    class Lab:
        pass

    L = Lab()
    L.data, L.home, L.store, L.tmp = data, home, store, tmp_path
    L.link_json = data / "buddy" / "link.json"

    def run(*argv, stdin=None, tty=True):
        out, err = io.StringIO(), io.StringIO()
        code = link.main(list(argv), stdin=stdin if stdin is not None else _Stdin("yes\n", tty), stdout=out,
                         stderr=err, consent_store=store)
        return code, out.getvalue(), err.getvalue()

    L.run = run
    L.link = lambda *extra, **kw: run("link", "--buddy-home", str(home), *extra, **kw)
    return L


def _cred(lab) -> str:
    for line in (lab.home / "secrets.env").read_text().splitlines():
        if line.startswith("BUDDY_EDGE_CREDENTIAL="):
            return line.split("=", 1)[1]
    raise AssertionError("no credential line")


def _mode(p) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


# ---------------------------------------------------------------------------
# the credential
# ---------------------------------------------------------------------------

def test_mint_credential_shape_and_entropy():
    a, b = link.mint_credential(), link.mint_credential()
    assert CRED_RE.fullmatch(a) and len(a) == 48
    assert a != b
    raw = base64.urlsafe_b64decode(a[5:] + "=")
    assert len(raw) == 32


def test_credential_id_is_the_first_eight_hex_of_the_sha256():
    c = link.mint_credential()
    sha = hashlib.sha256(c.encode()).hexdigest()
    assert link.credential_sha256(c) == sha and link.credential_id(sha) == sha[:8]


def test_verification_is_constant_time_and_rejects_anything_else():
    import inspect
    assert "compare_digest" in inspect.getsource(link.verify_credential)


# ---------------------------------------------------------------------------
# link: the happy path
# ---------------------------------------------------------------------------

def test_link_records_consent_and_writes_both_sides(lab):
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 0, out + err
    cred = _cred(lab)
    assert CRED_RE.fullmatch(cred)
    # the credential is shown nowhere
    assert cred not in out and cred not in err

    rec = json.loads(lab.link_json.read_text())
    sha = hashlib.sha256(cred.encode()).hexdigest()
    assert rec["schema"] == "arail.buddy-link/v1"
    assert rec["credential_sha256"] == sha and rec["credential_id"] == sha[:8]
    assert rec["buddy_home"] == str(lab.home.resolve())
    assert rec["edge_base_url"] == "http://127.0.0.1:8089/api/host/v1"
    assert rec["contract_sha256"] == CONTRACT_SHA256
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", rec["linked_at"])
    assert "credential" not in rec and cred not in lab.link_json.read_text()

    buddy_link = json.loads((lab.home / "link.json").read_text())
    assert buddy_link == {
        "schema": "qukaizen-buddy.link/v1", "edge_base_url": rec["edge_base_url"],
        "credential_id": sha[:8], "contract_sha256": CONTRACT_SHA256, "linked_at": rec["linked_at"],
    }
    assert cred not in (lab.home / "link.json").read_text()


def test_link_prints_the_consent_text_verbatim(lab):
    _, out, _ = lab.run("link", "--buddy-home", str(lab.home))
    for paragraph in (
        "Buddy on your phone needs the internet. Your lab will run in hybrid mode. In hybrid mode, the lab may "
        "also use cloud providers you set up. Buddy himself uses only your local model.",
        "Your messages to Buddy and his replies pass through Telegram's servers. Telegram can read them. Bot "
        "chats are not end-to-end encrypted.",
        "You can switch back to airgapped at any time. Buddy stops listening within one minute.",
    ):
        assert paragraph in out.replace("\n> ", " ").replace("\n", " ") or paragraph in out
    assert link.CONSENT_TEXT in out


def test_link_records_the_consent_the_operator_gave(lab):
    code, *_ = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 0
    assert lab.store.is_allowed("https://api.telegram.org/")
    rec = json.loads(lab.link_json.read_text())
    assert lab.store.is_approved(rec["consent_request_id"])
    hist = json.loads((lab.store.history_file).read_text())
    mine = [r for r in hist if r["id"] == rec["consent_request_id"]][0]
    assert mine["agent"] == "buddy" and mine["reason"] == "Buddy on Telegram"
    assert mine["domain"] == "api.telegram.org" and mine["status"] == "approved"
    assert lab.store.list_pending() == []


def test_when_the_domain_was_already_allowed_the_request_is_auto_approved_and_still_recorded(lab):
    lab.store.add_domain("https://api.telegram.org/")
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    rec = json.loads(lab.link_json.read_text())
    assert lab.store.is_approved(rec["consent_request_id"])


def test_file_modes_are_owner_only(lab):
    old = os.umask(0)
    try:
        assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    finally:
        os.umask(old)
    assert _mode(lab.link_json) == 0o600
    assert _mode(lab.link_json.parent) == 0o700
    assert _mode(lab.home / "secrets.env") == 0o600
    assert _mode(lab.home / "link.json") == 0o600
    assert _mode(lab.home) == 0o700
    assert not list(lab.home.glob(".*.tmp")) and not list(lab.link_json.parent.glob(".*.tmp"))


def test_an_existing_secrets_file_keeps_its_other_lines(lab):
    sec = lab.home / "secrets.env"
    sec.write_text("BUDDY_TELEGRAM_TOKEN=123:abc\nBUDDY_PSEUDONYM_SALT=" + "ab" * 32 + "\n")
    os.chmod(sec, 0o600)
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    lines = sec.read_text().splitlines()
    assert lines[0] == "BUDDY_TELEGRAM_TOKEN=123:abc" and lines[1].startswith("BUDDY_PSEUDONYM_SALT=")
    assert sum(l.startswith("BUDDY_EDGE_CREDENTIAL=") for l in lines) == 1
    assert _mode(sec) == 0o600


def test_the_portal_port_and_bind_address_no_longer_decide_the_url(lab, monkeypatch):
    """Until S5 the URL could name the portal's own port (PORTAL_PORT) or a bracketed IPv6 host; the portal serves no edge now, so it is always the edge app on 127.0.0.1."""
    monkeypatch.setenv("PORTAL_PORT", "9123")
    monkeypatch.setenv("BIND_ADDR", "::1")
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    assert json.loads(lab.link_json.read_text())["edge_base_url"] == "http://127.0.0.1:8089/api/host/v1"


def test_verify_credential_accepts_only_the_minted_one(lab):
    assert link.verify_credential(_cred_after_link(lab)) is not None
    assert link.verify_credential("bhc1_" + "A" * 43) is None
    assert link.verify_credential("") is None
    assert link.verify_credential(None) is None
    assert link.verify_credential("x" * 5000) is None


def _cred_after_link(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    return _cred(lab)


# ---------------------------------------------------------------------------
# link: refusals (exit 3, nothing written, nothing consented)
# ---------------------------------------------------------------------------

def _nothing_happened(lab):
    assert not lab.link_json.exists()
    assert not (lab.home / "secrets.env").exists() or "BUDDY_EDGE_CREDENTIAL" not in (lab.home / "secrets.env").read_text()
    assert not (lab.home / "link.json").exists()
    assert not lab.store.is_allowed("https://api.telegram.org/")
    assert lab.store.list_pending() == []


def test_refused_on_a_world_instance(lab, monkeypatch):
    monkeypatch.setenv("ARAIL_INSTANCE", "some-world")
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 3 and "root lab" in (out + err).lower()
    _nothing_happened(lab)


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.5", "::", "example.test"])
def test_refused_when_the_portal_is_not_bound_to_loopback(lab, monkeypatch, bind):
    monkeypatch.setenv("BIND_ADDR", bind)
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 3 and "loopback" in (out + err).lower()
    _nothing_happened(lab)


def test_refused_when_the_buddy_home_is_missing(lab):
    code, out, err = lab.run("link", "--buddy-home", str(lab.tmp / "nope"))
    assert code == 3 and "does not exist" in (out + err).lower()
    assert not lab.link_json.exists() and not lab.store.is_allowed("https://api.telegram.org/")


def test_refused_when_the_buddy_home_is_a_symlink(lab):
    ln = lab.tmp / "home-link"
    ln.symlink_to(lab.home)
    code, out, err = lab.run("link", "--buddy-home", str(ln))
    assert code == 3 and "symlink" in (out + err).lower()
    _nothing_happened(lab)


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o770, 0o777, 0o500, 0o600])
def test_refused_when_the_buddy_home_is_not_0700(lab, mode):
    os.chmod(lab.home, mode)
    try:
        code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    finally:
        os.chmod(lab.home, 0o700)
    assert code == 3 and "0700" in (out + err)
    _nothing_happened(lab)


def test_refused_when_the_buddy_home_belongs_to_someone_else(lab, monkeypatch):
    monkeypatch.setattr(link, "_current_uid", lambda: os.getuid() + 1)
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 3 and "owned" in (out + err).lower()
    _nothing_happened(lab)


def test_refused_when_the_buddy_home_is_a_file(lab):
    f = lab.tmp / "afile"
    f.write_text("x")
    assert lab.run("link", "--buddy-home", str(f))[0] == 3


def test_refused_when_secrets_env_is_a_symlink(lab):
    target = lab.tmp / "elsewhere.env"
    target.write_text("")
    os.chmod(target, 0o600)
    (lab.home / "secrets.env").symlink_to(target)
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 3 and "symlink" in (out + err).lower()
    assert target.read_text() == "" and not lab.link_json.exists()
    assert not lab.store.is_allowed("https://api.telegram.org/")


def test_refused_when_secrets_env_is_not_0600(lab):
    sec = lab.home / "secrets.env"
    sec.write_text("A=1\n")
    os.chmod(sec, 0o644)
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 3 and "0600" in (out + err)
    assert sec.read_text() == "A=1\n" and not lab.link_json.exists()


def test_refused_when_buddys_link_json_is_a_symlink(lab):
    target = lab.tmp / "elsewhere.json"
    target.write_text("{}")
    (lab.home / "link.json").symlink_to(target)
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 3
    assert target.read_text() == "{}" and not lab.link_json.exists()


def test_refused_when_the_labs_buddy_dir_is_a_symlink(lab):
    other = lab.tmp / "other-dir"
    other.mkdir()
    (lab.data / "buddy").symlink_to(other)
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 3 and "symlink" in (out + err).lower()
    assert list(other.iterdir()) == []


def test_refused_when_already_linked_without_rotate(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    first = lab.link_json.read_text()
    code, out, err = lab.run("link", "--buddy-home", str(lab.home))
    assert code == 3 and "already linked" in (out + err).lower() and "--rotate" in (out + err)
    assert lab.link_json.read_text() == first


def test_refused_without_a_tty_and_without_yes(lab):
    code, out, err = lab.run("link", "--buddy-home", str(lab.home), stdin=_Stdin("yes\n", tty=False))
    assert code == 3 and "--yes" in (out + err)
    _nothing_happened(lab)


def test_yes_flag_works_without_a_tty_and_still_prints_the_consent_text(lab):
    code, out, err = lab.run("link", "--buddy-home", str(lab.home), "--yes", stdin=_Stdin("", tty=False))
    assert code == 0, out + err
    assert link.CONSENT_TEXT in out
    assert lab.link_json.exists()


@pytest.mark.parametrize("answer", ["no\n", "\n", "y\n", "YES PLEASE\n", ""])
def test_anything_but_yes_is_a_refusal(lab, answer):
    code, out, err = lab.run("link", "--buddy-home", str(lab.home), stdin=_Stdin(answer, tty=True))
    assert code == 3 and "not linked" in (out + err).lower()
    _nothing_happened(lab)


def test_missing_buddy_home_argument_is_a_usage_error(lab):
    code, out, err = lab.run("link")
    assert code == 2


def test_unknown_verb_is_a_usage_error(lab):
    assert lab.run("frobnicate")[0] == 2
    assert lab.run()[0] == 2


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------

def test_rotate_replaces_the_credential_and_the_old_one_dies_at_once(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    old = _cred(lab)
    rec0 = json.loads(lab.link_json.read_text())
    assert link.verify_credential(old) is not None
    code, out, err = lab.run("link", "--rotate", "--buddy-home", str(lab.home), stdin=_Stdin("", tty=False))
    assert code == 0, out + err
    new = _cred(lab)
    assert new != old and CRED_RE.fullmatch(new)
    assert link.verify_credential(old) is None
    assert link.verify_credential(new) is not None
    rec1 = json.loads(lab.link_json.read_text())
    assert rec1["credential_id"] != rec0["credential_id"]
    assert rec1["consent_request_id"] == rec0["consent_request_id"]
    assert rec1["linked_at"] != "" and rec1["buddy_home"] == rec0["buddy_home"]
    assert json.loads((lab.home / "link.json").read_text())["credential_id"] == rec1["credential_id"]
    assert old not in out + err and new not in out + err
    assert sum(l.startswith("BUDDY_EDGE_CREDENTIAL=") for l in (lab.home / "secrets.env").read_text().splitlines()) == 1


def test_rotate_keeps_the_other_secrets(lab):
    sec = lab.home / "secrets.env"
    sec.write_text("BUDDY_TELEGRAM_TOKEN=123:abc\n")
    os.chmod(sec, 0o600)
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    assert lab.run("link", "--rotate", "--buddy-home", str(lab.home))[0] == 0
    assert "BUDDY_TELEGRAM_TOKEN=123:abc" in sec.read_text().splitlines()
    assert _mode(sec) == 0o600


def test_rotate_without_a_link_is_refused(lab):
    code, out, err = lab.run("link", "--rotate", "--buddy-home", str(lab.home))
    assert code == 3 and "not linked" in (out + err).lower()


def test_rotate_has_the_same_placement_checks(lab, monkeypatch):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    before = _cred(lab)
    monkeypatch.setenv("ARAIL_INSTANCE", "w")
    assert lab.run("link", "--rotate", "--buddy-home", str(lab.home))[0] == 3
    monkeypatch.delenv("ARAIL_INSTANCE")
    monkeypatch.setenv("BIND_ADDR", "0.0.0.0")
    assert lab.run("link", "--rotate", "--buddy-home", str(lab.home))[0] == 3
    assert _cred(lab) == before


# ---------------------------------------------------------------------------
# load_link: a corrupt or missing file is "not linked"; changes are noticed
# ---------------------------------------------------------------------------

def test_load_link_notices_a_rewrite_and_a_deletion(lab):
    assert link.load_link() is None
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    first = link.load_link()
    assert first and first["credential_id"]
    assert lab.run("link", "--rotate", "--buddy-home", str(lab.home))[0] == 0
    second = link.load_link()
    assert second["credential_sha256"] != first["credential_sha256"]
    lab.link_json.unlink()
    assert link.load_link() is None


@pytest.mark.parametrize("text", ["", "{", "[]", '{"schema": "other"}',
                                  json.dumps({"schema": "arail.buddy-link/v1", "credential_sha256": "short"})])
def test_a_corrupt_link_is_not_linked(lab, text):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    cred = _cred(lab)
    lab.link_json.write_text(text)
    assert link.load_link() is None
    assert link.verify_credential(cred) is None


def test_a_symlinked_link_file_is_not_linked(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    cred = _cred(lab)
    real = lab.tmp / "real.json"
    real.write_text(lab.link_json.read_text())
    lab.link_json.unlink()
    lab.link_json.symlink_to(real)
    assert link.verify_credential(cred) is None


# ---------------------------------------------------------------------------
# unlink
# ---------------------------------------------------------------------------

def test_unlink_revokes_everything_on_the_arail_side(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    cred = _cred(lab)
    code, out, err = lab.run("unlink")
    assert code == 0, out + err
    assert not lab.link_json.exists()
    assert not lab.store.is_allowed("https://api.telegram.org/")
    assert link.verify_credential(cred) is None
    assert (lab.home / "secrets.env").exists()  # no PATH given: Buddy's files are not touched


def test_unlink_with_the_buddy_home_also_cleans_buddys_side(lab):
    sec = lab.home / "secrets.env"
    sec.write_text("BUDDY_TELEGRAM_TOKEN=123:abc\n")
    os.chmod(sec, 0o600)
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    code, out, err = lab.run("unlink", "--buddy-home", str(lab.home))
    assert code == 0, out + err
    assert not (lab.home / "link.json").exists()
    assert sec.read_text().splitlines() == ["BUDDY_TELEGRAM_TOKEN=123:abc"]
    assert not lab.link_json.exists()


def test_unlink_when_not_linked_is_a_quiet_success(lab):
    code, out, err = lab.run("unlink")
    assert code == 0 and "not linked" in (out + err).lower()


def test_unlink_revokes_arail_even_if_the_buddy_home_is_unsafe(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    os.chmod(lab.home, 0o755)
    try:
        code, out, err = lab.run("unlink", "--buddy-home", str(lab.home))
        assert code == 3
        assert not lab.link_json.exists() and not lab.store.is_allowed("https://api.telegram.org/")
        assert (lab.home / "link.json").exists()  # refused to touch an unsafe directory
    finally:
        os.chmod(lab.home, 0o700)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_when_not_linked(lab):
    code, out, _ = lab.run("status", "--json")
    data = json.loads(out)
    assert code == 0 and data["linked"] is False and data["credential_id"] is None


def test_status_never_shows_the_credential_or_its_hash(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    cred = _cred(lab)
    sha = hashlib.sha256(cred.encode()).hexdigest()
    code, out, err = lab.run("status", "--json")
    data = json.loads(out)
    assert code == 0 and data["linked"] is True and data["credential_id"] == sha[:8]
    assert data["consent"] == {"api.telegram.org": True}
    assert data["contract_sha256"] == CONTRACT_SHA256 and data["contract_current"] is True
    assert cred not in out + err and sha not in out + err
    code, text, _ = lab.run("status")
    assert code == 0 and sha[:8] in text and cred not in text and sha not in text


def test_status_says_when_the_link_predates_the_current_contract(lab):
    assert lab.run("link", "--buddy-home", str(lab.home))[0] == 0
    rec = json.loads(lab.link_json.read_text())
    rec["contract_sha256"] = "0" * 64
    lab.link_json.write_text(json.dumps(rec))
    data = json.loads(lab.run("status", "--json")[1])
    assert data["contract_current"] is False


# ---------------------------------------------------------------------------
# arailctl and the module entry point
# ---------------------------------------------------------------------------

def test_arailctl_dispatches_buddy_to_the_module_like_world_and_nucleus():
    src = (REPO / "arailctl").read_text()
    assert re.search(r"^\s+buddy\)\n(?:.*\n){0,6}?.*exec python -m arail\.buddy_host \"\$@\"", src, re.M)
    assert "buddy <verb>" in src


def test_the_module_runs_as_a_script_and_exits_zero_on_status(tmp_path):
    env = {**os.environ, "ARAIL_DATA_DIR": str(tmp_path), "PYTHONPATH": str(REPO / "src")}
    r = subprocess.run([sys.executable, "-m", "arail.buddy_host", "status", "--json"], capture_output=True, text=True,
                       env=env, cwd=tmp_path, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["linked"] is False


# --- S4: the link names the box-only edge app --------------------------------------------------
def test_link_writes_the_edge_app_url_on_8089(lab, monkeypatch):
    monkeypatch.delenv("ARAIL_EDGE_PORT", raising=False)
    code, _, _ = lab.link()
    assert code == 0
    assert json.loads(lab.link_json.read_text())["edge_base_url"] == "http://127.0.0.1:8089/api/host/v1"
    assert json.loads((lab.home / "link.json").read_text())["edge_base_url"] == "http://127.0.0.1:8089/api/host/v1"


def test_link_refuses_with_the_edge_app_off_and_writes_nothing(lab, monkeypatch):
    """S5 (C5.2): ARAIL_EDGE_PORT=0 used to fall back to the portal port. The portal serves no edge now, so there is nowhere to point Buddy: exit 3 before a byte is
    written or a consent is recorded."""
    monkeypatch.setenv("ARAIL_EDGE_PORT", "0")
    code, out, err = lab.link()
    assert code == 3 and "Buddy needs the edge app; set ARAIL_EDGE_PORT" in err
    assert not lab.link_json.exists() and not (lab.home / "link.json").exists() and not (lab.home / "secrets.env").exists()
    assert "Type yes" not in out                                  # refused before the consent text and the confirmation
    code, _, err = lab.run("link", "--rotate", "--buddy-home", str(lab.home))
    assert code == 3 and "Buddy needs the edge app" in err


def test_rotate_keeps_a_working_8089_link_and_kills_the_old_credential(lab, monkeypatch):
    assert lab.link()[0] == 0
    old_cred = _cred(lab)
    assert link.verify_credential(old_cred) is not None
    assert lab.run("link", "--rotate", "--buddy-home", str(lab.home))[0] == 0
    assert json.loads(lab.link_json.read_text())["edge_base_url"] == "http://127.0.0.1:8089/api/host/v1"
    assert link.verify_credential(old_cred) is None             # rotation still kills the old credential


def test_arail_edge_port_override_is_followed(lab, monkeypatch):
    monkeypatch.setenv("ARAIL_EDGE_PORT", "9071")
    lab.link()
    assert json.loads(lab.link_json.read_text())["edge_base_url"] == "http://127.0.0.1:9071/api/host/v1"
