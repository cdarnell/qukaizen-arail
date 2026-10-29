"""The fake deep runtime does what its docstring says.

A test double that is wrong would hide a real result later, so each mode is
checked once here, through ARAIL's own ``AeroLLMBackend`` where that matters.
"""
from __future__ import annotations

import signal
import subprocess
import sys

import pytest

from tests.fakes.fake_deep_runtime import BUG_FRAGMENT, fake_deep  # noqa: F401  (fixture)


def _gen(fd, n: int, **env) -> subprocess.CompletedProcess:
    code = ("import aerollm_api\n"
            "rt = aerollm_api.Runtime('m')\nrt.start()\n"
            f"for i in range({n}):\n    print(rt.generate('p'), flush=True)\n")
    return subprocess.run([sys.executable, "-c", code], env=fd.env(**env),
                          capture_output=True, text=True, timeout=60)


def test_pass_mode_answers_every_call(fake_deep):
    fd = fake_deep("pass")
    be = fd.backend()
    for _ in range(3):
        assert be.complete("hello").text
    assert [c["n"] for c in fd.gen_calls()] == [1, 2, 3]


def test_fail2_answers_once_then_raises_the_real_message(fake_deep):
    fd = fake_deep("fail2")
    be = fd.backend()
    assert be.complete("hello").text
    for _ in range(2):
        with pytest.raises(RuntimeError) as ei:
            be.complete("hello")
        assert BUG_FRAGMENT in str(ei.value) and "has 1 elements" in str(ei.value)


def test_empty2_answers_once_then_returns_empty_text(fake_deep):
    fd = fake_deep("empty2")
    be = fd.backend()
    assert be.complete("hello").text
    assert be.complete("hello").text == ""


def test_loadfail_raises_while_the_backend_is_built(fake_deep):
    fd = fake_deep("loadfail")
    with pytest.raises(RuntimeError, match="could not start"):
        fd.backend()


def test_slow_mode_waits_then_answers(fake_deep):
    fd = fake_deep("slow:0.25")
    resp = fd.backend().complete("hello")
    assert resp.text and resp.latency_ms >= 240


def test_crash3_kills_the_process_with_sigkill_by_default(fake_deep):
    fd = fake_deep("crash3")
    r = _gen(fd, 3)
    assert r.returncode == -signal.SIGKILL, "SIGABRT would write a macOS crash report; the default avoids it"
    assert r.stdout.count("fake deep answer") == 2


def test_hang2_never_returns_from_call_2(fake_deep):
    fd = fake_deep("hang2")
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-c",
                        "import aerollm_api\nrt = aerollm_api.Runtime('m'); rt.start()\n"
                        "rt.generate('a'); rt.generate('b')\n"],
                       env=fd.env(), capture_output=True, text=True, timeout=3)


def test_stdout_noise_reaches_fd_1(fake_deep):
    fd = fake_deep("stdout-noise")
    r = _gen(fd, 1)
    assert "native banner on stdout" in r.stdout and "native noise during call 1" in r.stdout


def test_a_new_native_file_is_a_new_binary(fake_deep):
    fd = fake_deep()
    a = fd.sha256
    fd.rewrite_native(b"another build\n" * 10)
    assert fd.sha256 != a


def test_the_subprocess_env_points_at_a_scratch_lab(fake_deep):
    fd = fake_deep()
    env = fd.env()
    assert env["ARAIL_DATA_DIR"].startswith(str(fd.root))
    assert env["LAB_ROOT"].startswith(str(fd.root))
    assert "ARAIL_INSTANCE" not in env
    assert str(fd.pkg_dir) in env["PYTHONPATH"].split(":")
    assert env["ARAIL_DEEP_STATE_DIR"], "the autouse fixture's per-test store dir is inherited"


def test_calling_the_fixture_twice_switches_the_mode(fake_deep):
    a = fake_deep("pass")
    b = fake_deep("fail2")
    assert a is b
    be = a.backend()
    be.complete("x")
    with pytest.raises(RuntimeError):
        be.complete("y")
