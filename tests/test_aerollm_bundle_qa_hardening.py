"""QA pass — adversarial coverage for the BUNDLED AeroLLM install channel.

Companion to `tests/test_aerollm_bundle_install.py` (the builder's suite) and
`tests/test_aerollm_bundle_compliance.py` (licence drift). Everything here
was written *after* the three architect review rounds, targeting the classes
those rounds did not exercise:

  * malformed / hostile archive contents (truncated gzip, non-archive bytes,
    `../` traversal members)
  * a well-formed bundle carrying a `.so` that cannot load (wrong arch)
  * the F7 provenance guard misfiring on an *interrupted* bundle install
    rather than on a real DEV/RELEASE install
  * corrupt/empty provenance markers reaching `status`
  * the `auto` dispatch trap in ARCHITECTURE.md §9.1 step 3 — setting
    `AEROLLM_INDEX_URL` to an unreachable host to "prevent a RELEASE
    fallback" actually *selects* RELEASE
  * the digest-semantics mismatch between `BUNDLE.json.sha256` (a digest of
    the `.so`) and `AEROLLM_BUNDLE_SHA256` (a digest of the *tarball*)

No test hits the real network: every download path is served from a local
tarball via `AEROLLM_BUNDLE_FILE`, or is asserted to fail before `curl` runs.

See sprints/2026-08-05-arail-bundled-aerollm/TEST_REPORT.md.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BUILD = REPO / "scripts" / "build-aerollm.sh"

darwin_arm64 = pytest.mark.skipif(
    not (sys.platform == "darwin" and os.uname().machine == "arm64"),
    reason="the BUNDLED channel is macOS-arm64-only (F4 refuses elsewhere)",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _fresh_venv(tmp_path: pathlib.Path, name: str = "venv") -> pathlib.Path:
    """An empty interpreter with no aerollm_api — an 'outside user' python.

    Function-scoped on purpose: several tests here deliberately complete or
    half-complete an install into site-packages, so no venv may be shared.
    """
    venv_dir = tmp_path / name
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    return venv_dir / "bin" / "python3"


def _site_packages(python: pathlib.Path) -> pathlib.Path:
    out = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('platlib'))"],
        capture_output=True, text=True, check=True,
    )
    return pathlib.Path(out.stdout.strip())


def _run(mode, python, env_extra=None, args=()):
    env = {
        **os.environ,
        "NO_COLOR": "1",
        "ARAIL_AEROLLM_REPO": "/nonexistent",
        "PYTHON": str(python),
        **(env_extra or {}),
    }
    # Never let the ambient shell's channel/index knobs steer these tests.
    for leaky in ("AEROLLM_CHANNEL", "AEROLLM_BUNDLE_URL", "AEROLLM_BUNDLE_SHA256"):
        if env_extra is None or leaky not in env_extra:
            env.pop(leaky, None)
    return subprocess.run(
        ["bash", str(BUILD), mode, *args],
        capture_output=True, text=True, env=env,
    )


def _sidecar(tarball: pathlib.Path) -> pathlib.Path:
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    side = tarball.with_suffix(tarball.suffix + ".sha256")
    side.write_text(f"{digest}  {tarball.name}\n")
    return side


def _bundle(tmp_path: pathlib.Path, *, so_bytes: bytes, name="bundle.tar.gz",
            extra_members=()) -> pathlib.Path:
    """A structurally valid bundle tarball with a caller-chosen payload."""
    stage = tmp_path / f"stage-{name}"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "aerollm_api.abi3.so").write_bytes(so_bytes)
    (stage / "LICENSE").write_text("Apache-2.0\n")
    (stage / "NOTICE").write_text("AeroLLM NOTICE\n")
    (stage / "MANIFEST.json").write_text(json.dumps({
        "schema": "arail.aerollm-bundle/v1",
        "aerollm_version": "9.9.9",
        "aerollm_commit": "a" * 40,
        "aerollm_dirty": False,
        "built_at": "2026-01-01T00:00:00Z",
        "built_by": "qa",
        "platform": "macos-arm64",
        "python_abi": "abi3-cp39",
        "sha256": hashlib.sha256(so_bytes).hexdigest(),
        "license": "Apache-2.0",
        "modifications": "none",
        "arail_release": "vqa",
    }))
    tarball = tmp_path / name
    with tarfile.open(tarball, "w:gz") as tf:
        for f in ("aerollm_api.abi3.so", "LICENSE", "NOTICE", "MANIFEST.json"):
            tf.add(stage / f, arcname=f)
        for arcname, payload in extra_members:
            info = tarfile.TarInfo(arcname)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    _sidecar(tarball)
    return tarball


# --------------------------------------------------------------------------
# malformed / hostile archives  (60% edge-case bucket)
# --------------------------------------------------------------------------
@darwin_arm64
def test_truncated_archive_with_matching_digest_installs_nothing(tmp_path):
    """A download truncated by a dropped connection, whose sidecar was
    regenerated over the truncated bytes (or a partial write to a full disk),
    passes the checksum gate and must still fail closed at extraction."""
    good = _bundle(tmp_path, so_bytes=b"x" * 4096)
    trunc = tmp_path / "trunc.tar.gz"
    trunc.write_bytes(good.read_bytes()[: len(good.read_bytes()) // 2])
    _sidecar(trunc)

    py = _fresh_venv(tmp_path)
    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(trunc)})

    assert r.returncode != 0, r.stdout + r.stderr
    site = _site_packages(py)
    assert not (site / "aerollm_api.abi3.so").exists()
    assert not (site / "aerollm_api.bundle.json").exists()


@darwin_arm64
def test_non_archive_bytes_install_nothing(tmp_path):
    """AEROLLM_BUNDLE_FILE pointed at an HTML error page / random file."""
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"<html>404 Not Found</html>\n")
    _sidecar(junk)

    py = _fresh_venv(tmp_path)
    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(junk)})

    assert r.returncode != 0
    assert not (_site_packages(py) / "aerollm_api.abi3.so").exists()


@darwin_arm64
def test_traversal_member_cannot_escape_the_extraction_dir(tmp_path):
    """A tarball member named `../../ESCAPED` must not write outside the
    per-run mktemp dir, and must not result in an install."""
    sentinel_root = tmp_path / "sentinel_root"
    sentinel_root.mkdir()
    tar = _bundle(
        tmp_path,
        so_bytes=b"payload",
        name="trav.tar.gz",
        extra_members=[("../../../ESCAPED.txt", b"pwned\n")],
    )
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()

    py = _fresh_venv(tmp_path)
    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(tar), "TMPDIR": str(tmpdir)})

    assert r.returncode != 0, r.stdout + r.stderr
    escaped = list(tmp_path.rglob("ESCAPED.txt"))
    assert escaped == [], f"tar member escaped the extraction dir: {escaped}"
    assert not (_site_packages(py) / "aerollm_api.abi3.so").exists()


@darwin_arm64
@pytest.mark.parametrize("missing", ["aerollm_api.abi3.so", "MANIFEST.json", "LICENSE", "NOTICE"])
def test_bundle_missing_any_expected_member_is_refused(tmp_path, missing):
    stage = tmp_path / f"s-{missing}"
    stage.mkdir()
    members = {
        "aerollm_api.abi3.so": b"so",
        "MANIFEST.json": b"{}",
        "LICENSE": b"L",
        "NOTICE": b"N",
    }
    del members[missing]
    tarball = tmp_path / f"missing-{missing}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        for arc, payload in members.items():
            info = tarfile.TarInfo(arc)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    _sidecar(tarball)

    py = _fresh_venv(tmp_path)
    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(tarball)})

    assert r.returncode != 0
    assert missing in (r.stdout + r.stderr), r.stdout + r.stderr
    assert not (_site_packages(py) / "aerollm_api.abi3.so").exists()


@darwin_arm64
def test_unloadable_so_is_rolled_back_leaving_no_shadowing_artifact(tmp_path):
    """F1: a bundle whose `.so` is a valid archive member but not a loadable
    extension (wrong architecture, ABI drift, a Mach-O for another platform)
    must leave *nothing* behind — otherwise it shadows a future good install.
    """
    tar = _bundle(tmp_path, so_bytes=b"\xcf\xfa\xed\xfe" + b"\x00" * 2048)  # Mach-O magic, garbage body
    py = _fresh_venv(tmp_path)

    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(tar)})

    assert r.returncode != 0
    site = _site_packages(py)
    assert not (site / "aerollm_api.abi3.so").exists(), "broken .so left in place"
    assert not (site / "aerollm_api.bundle.json").exists(), "stale marker left in place"


# --------------------------------------------------------------------------
# provenance marker robustness
# --------------------------------------------------------------------------
@darwin_arm64
@pytest.mark.parametrize("marker_body", ["", "{not json,,,", '{"schema": null}', "\x00\x01\x02"])
def test_status_survives_a_corrupt_provenance_marker(tmp_path, marker_body):
    """A truncated/garbage `aerollm_api.bundle.json` must never crash
    `deep status`; it must degrade to the documented 'provenance not
    recorded' line and still exit 0."""
    py = _fresh_venv(tmp_path)
    site = _site_packages(py)
    (site / "aerollm_api.bundle.json").write_text(marker_body)

    r = _run("status", py)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "Traceback" not in (r.stdout + r.stderr)
    assert "channel:" in r.stdout


@darwin_arm64
def test_corrupt_marker_does_not_block_reinstall(tmp_path):
    """Self-healing: the idempotence short-circuit reads the marker; a
    corrupt one must fall through to a real reinstall, not wedge."""
    tar = _bundle(tmp_path, so_bytes=b"payload")
    py = _fresh_venv(tmp_path)
    site = _site_packages(py)
    (site / "aerollm_api.abi3.so").write_bytes(b"old")
    (site / "aerollm_api.bundle.json").write_text("{{{corrupt")

    # Neutralize the repo's own pyproject pin: this test's fabricated tarball
    # verifies against its sidecar, and the marker semantics are the subject.
    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(tar), "AEROLLM_PYPROJECT": "/nonexistent"})

    # The payload is not importable, so this ends in F1 rollback — the point
    # is that it got *past* the marker read rather than aborting on it.
    assert "Refusing to overwrite" not in (r.stdout + r.stderr), r.stdout + r.stderr
    assert "Checksum verified" in r.stdout, r.stdout + r.stderr


def test_standalone_route_self_serves_pyproject_pin(tmp_path):
    """R2-1: standalone `deep install` (no setup.sh forwarding) must still
    pick up the aerollm_bundle_sha256 pin from pyproject.toml — the
    fabricated tarball here matches its own sidecar but NOT the repo pin,
    so a pinned run must refuse it before install."""
    tar = _bundle(tmp_path, so_bytes=b"payload")
    py = _fresh_venv(tmp_path)

    # No AEROLLM_BUNDLE_SHA256 in env, no AEROLLM_PYPROJECT override: the
    # script must read the repo's own pyproject pin and reject the mismatch.
    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(tar)})

    assert r.returncode != 0
    assert "Checksum mismatch" in (r.stdout + r.stderr)
    site = _site_packages(py)
    assert not (site / "aerollm_api.abi3.so").exists()


# --------------------------------------------------------------------------
# dispatch semantics  (regression bucket)
# --------------------------------------------------------------------------
@darwin_arm64
def test_auto_selects_release_when_aerollm_index_url_is_overridden(tmp_path):
    """Regression guard for a trap in ARCHITECTURE.md §9.1 step 3.

    §9.1 tells the QA operator to point `AEROLLM_INDEX_URL` at an unreachable
    host "so a silent RELEASE fallback cannot rescue the run". But
    `_release_creds_configured()` treats *any* non-default
    `AEROLLM_INDEX_URL` as evidence of configured credentials, so that
    instruction selects RELEASE and never reaches the BUNDLED channel under
    test. This test pins the real behaviour so the acceptance recipe can be
    corrected against it rather than against an assumption.
    """
    py = _fresh_venv(tmp_path)
    r = _run("auto", py, {
        "AEROLLM_INDEX_URL": "https://unreachable-index.invalid/simple/",
        "AEROLLM_PIP_SPEC": "arail-qa-nonexistent-package-xyz",
    })
    combined = r.stdout + r.stderr
    assert "release channel" in combined, combined
    assert "bundled channel" not in combined, combined


@darwin_arm64
def test_auto_selects_bundled_with_no_sibling_and_no_index_override(tmp_path):
    """The headline outside-user journey: nothing configured → BUNDLED."""
    tar = _bundle(tmp_path, so_bytes=b"payload")
    py = _fresh_venv(tmp_path)
    r = _run("auto", py, {"AEROLLM_BUNDLE_FILE": str(tar)})
    assert "bundled channel" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("channel", ["dev", "release", "bundle"])
def test_aerollm_channel_accepts_exactly_the_three_documented_values(tmp_path, channel):
    """docs/cli.md advertises dev|release|bundle. Anything else must exit 2
    with the enumeration, and none of the three may be silently ignored."""
    py = _fresh_venv(tmp_path)
    r = _run("auto", py, {"AEROLLM_CHANNEL": channel, "AEROLLM_BUNDLE_FILE": "/nonexistent.tar.gz"})
    assert "must be one of" not in (r.stdout + r.stderr)


def test_unknown_aerollm_channel_exits_2_and_enumerates(tmp_path):
    py = _fresh_venv(tmp_path)
    for bad in ("bundled", "BUNDLE", "", " bundle"):
        if bad == "":
            continue  # empty means "auto", by design
        r = _run("auto", py, {"AEROLLM_CHANNEL": bad})
        assert r.returncode == 2, f"{bad!r}: {r.stdout + r.stderr}"
        assert "dev | release | bundle" in r.stderr


# --------------------------------------------------------------------------
# digest semantics  (security bucket)
# --------------------------------------------------------------------------
def test_committed_bundle_json_sha256_is_the_so_digest_not_the_tarball_digest():
    """`THIRD-PARTY-LICENSES/aerollm/BUNDLE.json`'s `sha256` field is the
    digest of `aerollm_api.abi3.so`, whereas `AEROLLM_BUNDLE_SHA256` is
    compared against the *tarball*. They are digests of different objects and
    are never equal.

    This matters because `docs/cli.md` currently points a security-conscious
    user at `BUNDLE.json` as the out-of-band value to pass to
    `AEROLLM_BUNDLE_SHA256` — which always fails with a scary 'Checksum
    mismatch — refusing to install'. This test pins the semantics so that
    whichever way the fix goes (add a tarball-digest pin, or verify the
    extracted `.so` against `MANIFEST.json.sha256`), the distinction stays
    explicit rather than being papered over.
    """
    bundle_json = json.loads(
        (REPO / "THIRD-PARTY-LICENSES" / "aerollm" / "BUNDLE.json").read_text()
    )
    pinned = bundle_json["sha256"]
    assert len(pinned) == 64 and all(c in "0123456789abcdef" for c in pinned)

    dist = REPO / "dist" / "aerollm-bundle"
    tarballs = sorted(dist.glob("*.tar.gz")) if dist.is_dir() else []
    if not tarballs:
        pytest.skip("no locally-built bundle in dist/ (maintainer-only artifact)")
    tarball = tarballs[0]

    tarball_digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    assert pinned != tarball_digest, (
        "BUNDLE.json.sha256 now equals the tarball digest — if that was "
        "intentional, docs/cli.md's AEROLLM_BUNDLE_SHA256 guidance and this "
        "test both need updating together."
    )

    with tarfile.open(tarball) as tf:
        so = tf.extractfile("aerollm_api.abi3.so")
        assert so is not None
        assert hashlib.sha256(so.read()).hexdigest() == pinned


@darwin_arm64
def test_no_digest_available_refuses_rather_than_installing(tmp_path):
    """Fail closed: a local tarball with no sidecar and no explicit pin must
    never be installed on trust."""
    tar = _bundle(tmp_path, so_bytes=b"payload")
    tar.with_suffix(tar.suffix + ".sha256").unlink()

    py = _fresh_venv(tmp_path)
    # Point AEROLLM_PYPROJECT at a nonexistent path so the repo's own
    # aerollm_bundle_sha256 pin can't supply a digest — the subject here is
    # the genuinely-no-digest-anywhere path.
    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(tar), "AEROLLM_PYPROJECT": "/nonexistent"})

    assert r.returncode != 0
    assert "unverified" in (r.stdout + r.stderr).lower()
    assert not (_site_packages(py) / "aerollm_api.abi3.so").exists()


@darwin_arm64
@pytest.mark.parametrize("url", [
    "http://github.com/x/y/releases/download/v1/a.tar.gz",
    "file:///etc/passwd",
    "ftp://example.com/a.tar.gz",
    "/etc/passwd",
    "javascript:alert(1)",
])
def test_non_https_bundle_url_is_refused_before_any_fetch(tmp_path, url):
    py = _fresh_venv(tmp_path)
    r = _run("bundle", py, {"AEROLLM_BUNDLE_URL": url})
    assert r.returncode != 0
    assert "non-https" in (r.stdout + r.stderr).lower(), r.stdout + r.stderr


# --------------------------------------------------------------------------
# F7 provenance guard — scope
# --------------------------------------------------------------------------
@darwin_arm64
def test_f7_guard_message_on_an_interrupted_bundle_install(tmp_path):
    """An install interrupted between the `.so` copy and the marker copy
    leaves an unmarked, unloadable `.so`. Re-running `deep install` then hits
    F7 and reports 'looks like a DEV or RELEASE install owns it' — which is
    false on a machine with neither.

    The behaviour is pinned here (refuses, names `--force`, installs nothing)
    so a follow-up fix that narrows the guard has a regression anchor. See
    TEST_REPORT.md finding Q6.
    """
    tar = _bundle(tmp_path, so_bytes=b"payload")
    py = _fresh_venv(tmp_path)
    site = _site_packages(py)
    (site / "aerollm_api.abi3.so").write_bytes(b"half-written")  # no marker

    r = _run("bundle", py, {"AEROLLM_BUNDLE_FILE": str(tar)})

    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "Refusing to overwrite" in combined
    assert "--force" in combined, "the guard must always name its own escape hatch"
    # And the artifact is untouched — the guard installs nothing either way.
    assert (site / "aerollm_api.abi3.so").read_bytes() == b"half-written"


# --------------------------------------------------------------------------
# platform guard
# --------------------------------------------------------------------------
def test_platform_guard_refuses_before_any_network_or_disk_write(tmp_path):
    """F4 must fire on a non-macOS-arm64 host. On a supported host we assert
    the complement: the guard does not fire and the run proceeds far enough
    to report the channel."""
    py = _fresh_venv(tmp_path)
    r = _run("bundle", py, {"AEROLLM_BUNDLE_URL": "http://never.invalid/x.tar.gz"})
    combined = r.stdout + r.stderr
    assert r.returncode != 0
    if sys.platform == "darwin" and os.uname().machine == "arm64":
        assert "macOS-arm64-only" not in combined
        assert "non-https" in combined.lower()
    else:
        assert "macOS-arm64-only" in combined
        assert "non-https" not in combined.lower()


# --------------------------------------------------------------------------
# onboarding contract
# --------------------------------------------------------------------------
def test_setup_failure_message_names_the_outside_user_route():
    """When `build-aerollm.sh auto` fails, `setup.sh` prints a remediation
    line. It must name `deep install` — the only route an outside user (no
    sibling repo, no private-index credentials) can actually take. Naming
    only `deep rebuild` / `deep update` sends them to two maintainer-only
    channels. See TEST_REPORT.md finding Q7.
    """
    setup = (REPO / "scripts" / "setup.sh").read_text()
    idx = setup.find("QueueLLM not installed")
    assert idx != -1, "the QueueLLM failure warning moved — retarget this test"
    window = setup[idx: idx + 400]
    assert "deep install" in window, (
        "setup.sh's QueueLLM failure message does not name `./arailctl deep "
        "install`, the outside-user route this sprint exists to add:\n" + window
    )


def test_cli_docs_disclose_the_sha256_trust_boundary():
    """The same-origin sidecar is integrity, not authenticity. That must be
    stated where a user configuring the channel will read it."""
    cli = (REPO / "docs" / "cli.md").read_text()
    assert "not** an\nauthenticity" in cli or "not** an authenticity" in cli.replace("\n", " ")
