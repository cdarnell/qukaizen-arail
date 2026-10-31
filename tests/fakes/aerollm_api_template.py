"""Fake aerollm_api for ARAIL tests. Pure Python. Not the product.

``tests/fakes/fake_deep_runtime.py`` copies this file to
``<tmp>/fakepkg/aerollm_api/__init__.py``. It has no ARAIL imports.

It imitates the one thing the deep sprint needs: a runtime that answers
call 1 and then fails (or not), by mode. The mode is read from
``ARAIL_FAKE_DEEP_MODE`` at every call, so a test can change it mid-run.

Modes:
  pass          every call answers
  fail2         call 1 answers; every later call raises the real bug's message
  empty2        call 1 answers; every later call returns an empty string
  panic2        call 1 answers; every later call raises PanicException, a BaseException
                that is not an Exception, as PyO3's pyo3_runtime.PanicException is
  crash3        call 3 kills the process with a signal (SIGKILL by default)
  hang2         call 2 never returns
  loadfail      Runtime.start() raises
  slow:<s>      every call sleeps <s> seconds, then answers
  stdout-noise  writes to fd 1 at import, at start and during calls, like native code

A real SIGABRT makes macOS write a crash report into the user's
DiagnosticReports folder, so the default crash signal is SIGKILL. Set
ARAIL_FAKE_DEEP_CRASH_SIGNAL=ABRT to opt in.
"""
import json
import os
import signal
import sys
import time
import types

__version__ = "0.0.0-fake"

_HERE = os.path.dirname(os.path.abspath(__file__))

# A native-looking module with a dummy extension file, so that identity
# works with no compiled code and no product seam.
_native = types.ModuleType(__name__ + "._native")
_native.__file__ = os.path.join(_HERE, "_native.abi3.so")
sys.modules[_native.__name__] = _native

BUG = (
    "backend `mlx-native` failed: apply_pre_block: Qwen2 layer (chunked prefill head): "
    "\"[rms_norm] (*weight) must have the same size as the last dimension of x "
    "but has 1 elements."
)


class PanicException(BaseException):
    """Stands in for pyo3_runtime.PanicException (a Rust panic crossing into Python), which derives
    from BaseException: ``except Exception`` does not catch it."""


# The instance most recently raised, so a test can check that it came out of ARAIL's
# backend as the same object and not a copy or a wrapper.
LAST_PANIC = None


def _mode():
    return os.environ.get("ARAIL_FAKE_DEEP_MODE", "pass")


if _mode() == "stdout-noise":
    os.write(1, b"native import banner on stdout\n")


def _log(event):
    path = os.environ.get("ARAIL_FAKE_DEEP_COUNT_FILE")
    if not path:
        return
    event = dict(event, pid=os.getpid(), t=time.time())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


class Runtime:
    def __init__(self, model_path, **kwargs):
        self.model_path = model_path
        self.kwargs = dict(kwargs)
        self.calls = 0
        self.started = False
        _log({"ev": "init", "kwargs": sorted(kwargs)})

    def start(self):
        mode = _mode()
        if mode == "loadfail":
            _log({"ev": "start_failed"})
            raise RuntimeError("fake runtime could not start: no Metal device")
        if mode == "stdout-noise":
            os.write(1, b"native banner on stdout\n")
            os.write(1, b'{"ev": "call", "i": 99, "ok": true}\n')
        self.started = True
        _log({"ev": "start"})

    def generate(self, prompt, max_new_tokens=512, temperature=0.7, **kw):
        global LAST_PANIC
        self.calls += 1
        n = self.calls
        mode = _mode()
        _log({"ev": "gen", "n": n, "mode": mode, "chars": len(prompt)})
        if mode == "stdout-noise":
            os.write(1, ("native noise during call %d\n" % n).encode())
        if mode.startswith("slow:"):
            time.sleep(float(mode.split(":", 1)[1]))
        if mode == "fail2" and n >= 2:
            raise RuntimeError(BUG)
        if mode == "empty2" and n >= 2:
            return ""
        if mode == "panic2" and n >= 2:
            LAST_PANIC = PanicException("called `Option::unwrap()` on a `None` value (fake native panic)")
            raise LAST_PANIC
        if mode == "crash3" and n == 3:
            os.write(2, b"native runtime: fatal error in layer 3 (test double)\n")
            name = os.environ.get("ARAIL_FAKE_DEEP_CRASH_SIGNAL", "KILL")
            os.kill(os.getpid(), getattr(signal, "SIG" + name))
            time.sleep(60)
        if mode == "hang2" and n == 2:
            while True:
                time.sleep(3600)
        return ("fake deep answer %d: the runtime is a test double and "
                "returns this fixed text." % n)
