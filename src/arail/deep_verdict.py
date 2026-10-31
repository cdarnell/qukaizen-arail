"""The deep verdict: does QueueLLM generate on the binary this process loaded?

This module owns one question and nothing else. It never generates. It never
constructs ``AeroLLMBackend``. It never raises to a caller. It uses no network.
It imports only the standard library at module level, so ``python -m
arail.deep_verdict`` runs under any interpreter the build script is given.

Why it exists (sprints/2026-10-29-arail-deep-honest): every QueueLLM build on
this Mac answers the first deep call in a process and fails the second. ARAIL's
status surfaces tested import, policy and residency, never generation, and the
one that recorded a real failure was erased by the next re-probe. So a failure
is now stored once, per binary, in one checkout-wide file, and only a passing
``./arailctl deep probe`` of the same binary clears it.

State is keyed by the sha256 of the *native* file the process loaded:

* ``verdict.json`` (schema ``arail.deep-verdict/v1``) lives in ``state_dir()``.
* A failure (a probe, a deep call, a load) sets "failing" for that binary.
* A later successful call clears nothing. Only ``record_probe`` with a 5/5
  pass clears a failure, and not one that could have landed while that probe
  ran: a failure last seen before the probe started is cleared, and so is one
  stamped in the future (the clock was stepped back) or not a time at all.
* A new binary has its own key. It starts "never tested", not "passing".

Writes take ``verdict.lock`` (flock, retried for up to 2 s), then write a
temp file and ``os.replace`` it. Reads are cached by the file's stat and never
take the lock. A corrupt or wrong-schema file fails closed: "failing", with
the path and the error in the detail. The next writer moves it aside.

The names below that mention the old product name are the frozen import
surface, kept in one place on purpose (the pattern of
``arail.nucleus.runtime_names``).
"""
from __future__ import annotations

import copy
import errno
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import math
import os
import random
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# frozen surface: never rename. `aerollm_api` is what ARAIL imports.
FROZEN_MODULE = "aerollm_api"
NATIVE_MODULE_NAMES = ("aerollm_api", "queuellm_api")   # names a native module may load under

SCHEMA = "arail.deep-verdict/v1"
MAX_BINARIES = 32
LOCK_TIMEOUT_S = 2.0
CLOCK_SLACK_S = 5.0     # how far ahead of "now" a failure's stamp may be and still be a failure of this moment
OPEN_ATTEMPTS = 5       # tries at creating a file in the state folder before believing it is gone (_open_in_dir)
HEALTH_PREFIX = "generation failing on this build"
UNIDENTIFIED = "unidentified"

_EXT_SUFFIXES = tuple(importlib.machinery.EXTENSION_SUFFIXES)

# ---------------------------------------------------------------------------
# Teaching. Every number this module prints carries its meaning, its bar and
# why, what out of range means, and where the failure lives (DAY1.json is the
# model). The probe, the Curator and the win script keep their own tables.
# ---------------------------------------------------------------------------
TEACHING: dict[str, dict[str, str]] = {
    "passed": {
        "meaning": "how many of the 5 sequential deep calls answered with non-empty text at the last probe, "
                   "in one fresh process",
        "threshold": "5 of 5, because every consumer makes more than one deep call per process and call 2 "
                     "is where every known broken build fails",
        "out_of_range": "1 of 5 means the runtime answers once per process (the call-2 bug); 0 of 5 means "
                        "it cannot answer at all; 2 to 4 is intermittent and counts as failing",
        "failure_lives_in": "the QueueLLM binary on the binary line, not ARAIL settings; read the first error",
    },
    "median_ok_s": {
        "meaning": "the middle wall time of the probe calls that answered: prefill plus up to 384 new tokens",
        "threshold": "none for the probe; an interactive deep answer should stay under 120 s",
        "out_of_range": "over 120 s means memory pressure or a slow build; no value means nothing answered",
        "failure_lives_in": "runtime decode speed, or memory on this Mac",
    },
    "failures": {
        "meaning": "failure events recorded for this binary since its last pass: a failed probe is one, "
                   "each failed deep call in any lab is one",
        "threshold": "0, because any failure keeps deep marked failing until a probe passes 5 of 5",
        "out_of_range": "1 or more means deep is failing; a high count means consumers kept calling it "
                        "before the failure was noticed",
        "failure_lives_in": "the consumer named in the record (agent, system label or probe) found it; "
                            "the fault is in the QueueLLM binary",
    },
}


# ---------------------------------------------------------------------------
# Value types (C1)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Binary:
    module: str          # "aerollm_api", the frozen name ARAIL imports
    resolves_to: str     # spec.origin: a package __init__.py or the extension itself
    native_path: str     # the one native extension the import loads
    sha256: str          # 64 hex
    bytes: int
    mtime: str           # ISO UTC
    version: str         # the module's __version__, or "unknown"
    pinned: bool         # True once this process has loaded it


@dataclass(frozen=True)
class Verdict:
    state: str                     # "failing" | "passing" | "untested"
    binary: Optional[Binary]
    detail: str                    # one sentence, user-facing
    since: Optional[str]           # ISO UTC
    source: Optional[str]          # "probe" | "call" | "load" | "process" | "store_unreadable"
    failure: Optional[dict]        # the C2 failure object
    last_probe: Optional[dict]     # the C2 last_probe object
    binary_changed_on_disk: bool
    store_error: Optional[str]


@dataclass
class _Pin:
    binary: Binary
    sig: tuple                      # (size, mtime_ns, inode) when it was loaded


class _Proc:
    """Everything this process remembers. Reset by ``_reset_for_tests``."""

    def __init__(self) -> None:
        self.pin: Optional[_Pin] = None
        self.ident_reason: str = ""
        self.mem: dict[str, dict] = {}        # key -> failure object kept in this process only
        self.mem_version = 0
        self.write_error: Optional[str] = None
        self.ok = 0
        self.failed = 0
        self.read_cache: Optional[tuple] = None   # (path, sig, doc, err)
        self.hash_cache: dict[tuple, str] = {}
        self.verdict_cache: Optional[tuple] = None


_lock = threading.RLock()
_P = _Proc()


def _reset_for_tests() -> None:
    """Test-only: forget the pinned identity, the caches and in-memory failures."""
    global _P
    with _lock:
        _P = _Proc()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _utc_minute(iso: Optional[str]) -> str:
    """'2026-10-29T13:35:02Z' -> '2026-10-29 13:35 UTC'."""
    if not iso or len(iso) < 16:
        return "an unknown time"
    return f"{iso[:10]} {iso[11:16]} UTC"


# What a terminal acts on: the C0 controls (NUL, BEL, BS, ESC ...), DEL, and the C1 range U+0080-U+009F
# (U+009B is a one-character CSI). Error text can come from the native runtime, from a stored verdict
# anyone with write access can edit, or from a package's metadata; none of it may move the cursor or erase
# the FAILING line (CWE-150).
_CONTROLS = {c: "?" for c in (*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0))}


def _printable(text: Any) -> str:
    """``text`` as one line a terminal shows without acting on it.

    Every C0 control (a newline or a bare CR too, because the caller prints exactly one line), DEL and every
    C1 control becomes ``?``. Nothing else changes: spaces, accents, symbols and punctuation pass through,
    whitespace is not collapsed and nothing is cut off (``_clip`` does both, before this)."""
    return str(text if text is not None else "").translate(_CONTROLS)


def _clip(text: Any, limit: int) -> str:
    """Error or label text, as one printable line of at most ``limit`` characters.

    Whitespace (newlines and tabs included) collapses to single spaces first, so a multi-line error keeps
    its words; the control characters that are left (ESC, BEL, DEL, C1 ...) become ``?``."""
    s = _printable(" ".join(str(text if text is not None else "").split()))
    if len(s) <= limit:
        return s
    return s[: max(limit - 1, 0)] + "…"


def _err_text(error: Any) -> str:
    if error is None:
        return "unknown error"
    if isinstance(error, BaseException):
        msg = str(error).strip()
        return f"{type(error).__name__}: {msg}" if msg else type(error).__name__
    return str(error)


def _file_sig(path: str) -> tuple:
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def _sha256_file(path: str) -> str:
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns, st.st_ino)
    cached = _P.hash_cache.get(key)
    if cached:
        return cached
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _P.hash_cache[key] = digest
    return digest


def _lab() -> str:
    return os.environ.get("ARAIL_INSTANCE", "").strip() or "root"


def _is_probe_child() -> bool:
    return os.environ.get("ARAIL_DEEP_PROBE_CHILD", "") == "1"


def _default_consumer() -> str:
    """Who is calling: deep-probe | agent:<id> | system:<label> | unattributed."""
    if _is_probe_child():
        return "deep-probe"
    try:
        from arail import agent_context
        src = agent_context.billing_source(agent_context.current())
    except Exception:  # noqa: BLE001
        return "unattributed"
    if src.startswith("sys:"):
        return "system:" + src[4:]
    return src


def _emit(level: str, message: str) -> None:
    """One activity line. Never raises, never recurses into this module."""
    try:
        from arail.activity import activity_log
        activity_log.emit("deep", message, level)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Identity (C1)
# ---------------------------------------------------------------------------
def _scan_loaded() -> tuple[list[str], str]:
    """Native modules in ``sys.modules`` under the known names. ``([paths], reason)``."""
    seen: dict[str, str] = {}
    for name, mod in list(sys.modules.items()):
        if name.split(".", 1)[0] not in NATIVE_MODULE_NAMES:
            continue
        f = getattr(mod, "__file__", None)
        if isinstance(f, str) and f.endswith(_EXT_SUFFIXES):
            seen.setdefault(os.path.realpath(f), f)
    paths = sorted(seen.values())
    if len(paths) == 1:
        return paths, ""
    if not paths:
        return [], "no native QueueLLM module is loaded"
    return paths, f"{len(paths)} native modules are loaded ({', '.join(paths)})"


def _module_version(native_path: str) -> str:
    top = sys.modules.get(FROZEN_MODULE)
    v = getattr(top, "__version__", None) if top is not None else None
    if not v:
        for name, mod in list(sys.modules.items()):
            if name.split(".", 1)[0] in NATIVE_MODULE_NAMES and getattr(mod, "__file__", None) == native_path:
                v = getattr(mod, "__version__", None)
                if v:
                    break
    return str(v) if v else "unknown"


def _make_binary(native_path: str, resolves_to: Optional[str], version: str, pinned: bool) -> Binary:
    st = os.stat(native_path)
    return Binary(
        module=FROZEN_MODULE,
        resolves_to=resolves_to or native_path,
        native_path=native_path,
        sha256=_sha256_file(native_path),
        bytes=st.st_size,
        mtime=_iso(st.st_mtime),
        version=version,
        pinned=pinned,
    )


def _find_spec_safe(name: str = FROZEN_MODULE):
    try:
        return importlib.util.find_spec(name)
    except Exception:  # noqa: BLE001 - ValueError, ImportError, a broken finder
        return None


def static_native(spec: Any = None) -> tuple[Optional[str], str]:
    """The native file ``find_spec`` says the frozen import resolves to, without importing.

    ``(path, reason)``. An extension origin is the file. In a package dir, the
    single extension file there is the file. Anything else gives ``(None, why)``.
    """
    spec = spec if spec is not None else _find_spec_safe()
    if spec is None or not getattr(spec, "origin", None):
        return None, f"{FROZEN_MODULE} is not importable by this interpreter"
    origin = spec.origin
    if origin.endswith(_EXT_SUFFIXES):
        return origin, ""
    locs = list(getattr(spec, "submodule_search_locations", None) or [])
    if not locs:
        return None, f"{origin} is not a package or an extension"
    try:
        exts = sorted(n for n in os.listdir(locs[0]) if n.endswith(_EXT_SUFFIXES))
    except OSError as e:
        return None, f"{locs[0]}: {e.strerror or e}"
    if len(exts) == 1:
        return os.path.join(locs[0], exts[0]), ""
    if not exts:
        return None, f"no native extension in {locs[0]}"
    return None, f"{len(exts)} native extensions in {locs[0]}"


def _static_identity() -> Optional[Binary]:
    spec = _find_spec_safe()
    native, reason = static_native(spec)
    if native is None:
        _P.ident_reason = reason
        return None
    _P.ident_reason = ""
    return _make_binary(native, spec.origin, "unknown", pinned=False)


def identity(*, import_if_needed: bool = True) -> Optional[Binary]:
    """The binary this process loaded, or None.

    Loaded: the one native module in ``sys.modules`` under a known name. The
    first loaded identity is pinned for the process. With
    ``import_if_needed=False`` and nothing loaded, the answer comes from
    ``find_spec`` alone (a static answer, never pinned, nothing imported).
    """
    try:
        with _lock:
            return _identity(import_if_needed)
    except Exception as e:  # noqa: BLE001
        _P.ident_reason = f"identity failed: {type(e).__name__}: {e}"
        return None


def _identity(import_if_needed: bool) -> Optional[Binary]:
    if _P.pin is not None:
        return _P.pin.binary
    natives, reason = _scan_loaded()
    if not natives and import_if_needed and _find_spec_safe() is not None:
        try:
            importlib.import_module(FROZEN_MODULE)
        except Exception:  # noqa: BLE001 - a failed import is "not identified", not an error here
            pass
        natives, reason = _scan_loaded()
    if len(natives) == 1:
        native = natives[0]
        top = sys.modules.get(FROZEN_MODULE)
        resolves = getattr(top, "__file__", None) if top is not None else None
        b = _make_binary(native, resolves if isinstance(resolves, str) else None,
                         _module_version(native), pinned=True)
        _P.pin = _Pin(b, _file_sig(native))
        _P.ident_reason = ""
        return b
    _P.ident_reason = reason
    if natives or import_if_needed:
        return None
    return _static_identity()


def _changed_on_disk() -> bool:
    pin = _P.pin
    if pin is None:
        return False
    try:
        return _file_sig(pin.binary.native_path) != pin.sig
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Where the store lives, and how it is read and written (C1, C2)
# ---------------------------------------------------------------------------
def _checkout_root() -> Optional[Path]:
    """The checkout this module runs from, or None (a pip install, a venv)."""
    try:
        root = Path(__file__).resolve().parents[2]
    except IndexError:
        return None
    if "site-packages" in root.parts or "dist-packages" in root.parts:
        return None
    return root if (root / "arailctl").is_file() else None


def state_dir() -> Path:
    """``ARAIL_DEEP_STATE_DIR``, else ``<checkout>/lab/data/deep``, else ``DATA_DIR/deep``."""
    env = os.environ.get("ARAIL_DEEP_STATE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    root = _checkout_root()
    if root is not None:
        return root / "lab" / "data" / "deep"
    from arail import config
    return Path(config.DATA_DIR).expanduser().absolute() / "deep"


def _store_file() -> Path:
    return state_dir() / "verdict.json"


def _empty_doc() -> dict:
    return {"schema": SCHEMA, "updated_at": _iso(_now()), "binaries": {}}


def _is_finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _bad_field(obj: dict, where: str, name: str, ok: Callable[[Any], bool], what: str) -> Optional[str]:
    """``where.name is not <what>`` when the field is present, not null, and fails *ok*."""
    v = obj.get(name)
    if v is not None and not ok(v):
        return f"{where}.{name} is not {what}"
    return None


def _validate_entry(sha: str, e: dict) -> Optional[str]:
    """What the readers rely on in one binary's record, and nothing more. An unknown extra key is kept."""
    where = f"binaries[{sha[:12]}]"
    f = e.get("failure")
    if f is not None:
        if not isinstance(f, dict):
            return f"{where}.failure is not null or an object"
        for name in ("since_ts", "last_ts"):
            bad = _bad_field(f, f"{where}.failure", name, _is_finite_number, "a finite number")
            if bad:
                return bad
        for name in ("since", "last_at"):
            bad = _bad_field(f, f"{where}.failure", name, lambda v: isinstance(v, str), "a string")
            if bad:
                return bad
        bad = _bad_field(f, f"{where}.failure", "count", _is_int, "a whole number")
        if bad:
            return bad
    lp = e.get("last_probe")
    if lp is not None:
        if not isinstance(lp, dict):
            return f"{where}.last_probe is not null or an object"
        checks = (("started_ts", _is_finite_number, "a finite number"),
                  ("at", lambda v: isinstance(v, str), "a string"),
                  ("started_at", lambda v: isinstance(v, str), "a string"),
                  ("passed", _is_int, "a whole number"), ("total", _is_int, "a whole number"),
                  ("first_error", lambda v: isinstance(v, dict), "an object"))
        for name, ok, what in checks:
            bad = _bad_field(lp, f"{where}.last_probe", name, ok, what)
            if bad:
                return bad
    return _bad_field(e, where, "last_pass_at", lambda v: isinstance(v, str), "a string")


def _validate_doc(doc: Any) -> Optional[str]:
    """``None`` when the document is safe to read; else why not. A store that fails here is unreadable
    (fail closed: deep reads as failing) and the next writer moves it aside."""
    if not isinstance(doc, dict):
        return "not a JSON object"
    if doc.get("schema") != SCHEMA:
        return f"unknown schema {doc.get('schema')!r} (expected {SCHEMA})"
    bins = doc.get("binaries")
    if not isinstance(bins, dict) or any(not isinstance(v, dict) for v in bins.values()):
        return "'binaries' is not an object of objects"
    for sha, entry in bins.items():
        bad = _validate_entry(str(sha), entry)
        if bad:
            return bad
    return None


def _read_store(*, use_cache: bool = True) -> tuple[Optional[dict], Optional[str]]:
    """``(doc, error)``. Missing is ``(None, None)``. Cached by stat; never locks.

    ``use_cache=False`` always reads the file. A writer holding the lock uses
    it, so a stale cache can never cost an update.
    """
    path = _store_file()
    try:
        lst = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):   # no file, or a parent that is not a directory
        _P.read_cache = None
        return None, None
    except OSError as e:
        _P.read_cache = None
        return None, f"{path}: {e.strerror or e}"
    if stat.S_ISLNK(lst.st_mode):
        _P.read_cache = None
        return None, f"{path}: is a symbolic link; the verdict file is never followed through one"
    sig = (lst.st_mtime_ns, lst.st_size, lst.st_ino)
    cached = _P.read_cache
    if use_cache and cached is not None and cached[0] == str(path) and cached[1] == sig:
        return cached[2], cached[3]
    doc: Optional[dict] = None
    err: Optional[str] = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read()
        parsed = json.loads(raw.decode("utf-8"))
        bad = _validate_doc(parsed)
        if bad:
            err = f"{path}: {bad}"
        else:
            doc = parsed
    except RecursionError:
        # json.loads raises this, not a ValueError, for JSON nested deeper than the interpreter allows.
        err = f"{path}: RecursionError: the JSON is nested too deeply to read"
    except Exception as e:  # noqa: BLE001 - OSError, bad UTF-8, bad JSON, anything: the file is unreadable
        err = f"{path}: {type(e).__name__}: {e}"
    _P.read_cache = (str(path), sig, doc, err)
    return doc, err


class StateDirRefused(OSError):
    """ARAIL will not write the verdict store in this folder. ``strerror`` is the whole sentence: what is
    wrong, why it matters and what to do."""

    def __init__(self, message: str) -> None:
        super().__init__(errno.EPERM, message)


def _my_uid() -> int:
    return os.getuid()


def _open_state_dir(d: Path) -> int:
    """Create the state folder if it is missing and return a file descriptor for it.

    The files of the store open with ``O_NOFOLLOW``, which guards only the last path component. When the
    folder itself is a symbolic link, ``makedirs`` and ``chmod`` follow it and every write lands in whatever
    it points at (QA-4), so the folder is checked with ``lstat`` before every write: a link is refused, and
    so is a folder this user does not own. It is then opened with ``O_NOFOLLOW``, and the lock, the temp
    file, the replace and the move-aside all go through that descriptor (``dir_fd``), so a link planted after
    the check cannot redirect them either. A symbolic link *above* the folder (a lab kept on another disk)
    is fine: only the last component is refused. Raises ``StateDirRefused`` or ``OSError``; the caller closes
    the descriptor."""
    try:
        lst = os.lstat(d)
    except FileNotFoundError:
        lst = None
    link = (f"{d} is a symbolic link, and ARAIL never writes the deep verdict through one. Replace it with "
            "a real folder, or set ARAIL_DEEP_STATE_DIR to a folder you own.")
    if lst is not None and stat.S_ISLNK(lst.st_mode):
        raise StateDirRefused(link)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        fd = os.open(d, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW)
    except OSError as e:
        # makedirs(exist_ok=True) has just passed, so the path is a folder or a link to one. A refusal of
        # O_NOFOLLOW|O_DIRECTORY is therefore a link: ELOOP on Linux, ENOTDIR on macOS. It may have appeared
        # between the lstat above and this open.
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise StateDirRefused(link) from e
        raise
    try:
        owner = os.fstat(fd).st_uid
        if owner != _my_uid():
            raise StateDirRefused(
                f"{d} is owned by another user (uid {owner}; you are uid {_my_uid()}), and ARAIL never writes "
                "the deep verdict into a folder that is not yours. Use a folder you own, or set "
                "ARAIL_DEEP_STATE_DIR to one.")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_in_dir(dir_fd: int, name: str, flags: int, mode: int = 0o600) -> int:
    """``os.open(name, flags, mode, dir_fd=dir_fd)`` that survives macOS losing a create race with itself.

    When several processes create the same file in one folder at the same instant, ``openat(O_CREAT)`` on
    macOS lets one of them win and fails the rest with ENOENT, although the folder is there and the file now
    exists (measured with eight processes: seven failed in nearly every round, and every one succeeded on the
    next try; the same call by path never failed). The store opens its files through the folder's descriptor
    (QA-4), so the lock file met it, and the write that did was reported as a failure and kept in memory. A
    second try opens the file the winner made. A folder that is really gone fails every try, and the last
    error is raised about 20 ms later. Any other error is raised at once."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return os.open(name, flags, mode, dir_fd=dir_fd)
        except FileNotFoundError:
            if attempt >= OPEN_ATTEMPTS:
                raise
            time.sleep(0.002 * attempt)


def _flock_acquire(fd: int) -> Optional[str]:
    import fcntl  # POSIX only; ARAIL runs on macOS, Linux and WSL
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return None
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return f"flock: {e.strerror or e}"
        if time.monotonic() >= deadline:
            return f"the verdict lock was busy for more than {LOCK_TIMEOUT_S:.0f} s"
        time.sleep(0.002 + random.random() * 0.006)


def _write_atomic(path: Path, doc: dict, dir_fd: Optional[int] = None) -> None:
    """Write *doc* to a temp file and replace *path* with it. With ``dir_fd`` (the state folder, from
    ``_open_state_dir``) every name is relative to that descriptor."""
    name = f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    tmp, target = (name, path.name) if dir_fd is not None else (str(path.with_name(name)), str(path))
    data = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
    except FileExistsError:           # a stale temp from a crashed writer with the same pid
        os.unlink(tmp, dir_fd=dir_fd)
        fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def _move_aside(path: Path, dir_fd: Optional[int] = None) -> None:
    """Rename an unreadable store to ``verdict.json.corrupt-<stamp>``, keeping it for a human."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(_now()))

    def where(name: str) -> str:
        return name if dir_fd is not None else str(path.with_name(name))

    def taken(name: str) -> bool:
        try:
            os.lstat(where(name), dir_fd=dir_fd)
            return True
        except FileNotFoundError:
            return False

    dest = f"{path.name}.corrupt-{stamp}"
    n = 1
    while taken(dest):
        n += 1
        dest = f"{path.name}.corrupt-{stamp}-{n}"
    os.rename(where(path.name), where(dest), src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def _locked_update(mutate: Callable[[dict], None]) -> Optional[str]:
    """Read, mutate and atomically replace ``verdict.json`` under the lock.

    Returns None on success, else a one-line error. The caller keeps the
    failure in memory when this returns an error.
    """
    d = state_dir()
    try:
        dir_fd = _open_state_dir(d)
    except StateDirRefused as e:
        return str(e.strerror)
    except OSError as e:
        return f"{d}: {e.strerror or e}"
    except Exception as e:  # noqa: BLE001 - never an exception in a caller: they keep the failure in memory
        return f"{d}: {type(e).__name__}: {e}"
    try:
        try:
            os.fchmod(dir_fd, 0o700)          # through the descriptor: never through a link
        except OSError:
            pass
        try:
            lock_fd = _open_in_dir(dir_fd, "verdict.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as e:
            return f"{d}: {e.strerror or e}"
        try:
            try:
                os.fchmod(lock_fd, 0o600)
            except OSError:
                pass
            err = _flock_acquire(lock_fd)
            if err:
                return err
            try:
                path = d / "verdict.json"
                doc, read_err = _read_store(use_cache=False)
                if read_err is not None:
                    try:
                        _move_aside(path, dir_fd)
                    except OSError as e:
                        return f"{path}: could not move the unreadable file aside ({e.strerror or e})"
                    doc = None
                if doc is None:
                    doc = _empty_doc()
                mutate(doc)
                doc["updated_at"] = _iso(_now())
                _write_atomic(path, doc, dir_fd)
                try:
                    lst = os.lstat(path)
                    _P.read_cache = (str(path), (lst.st_mtime_ns, lst.st_size, lst.st_ino), doc, None)
                except OSError:
                    _P.read_cache = None
                return None
            except OSError as e:
                return f"{d}: {e.strerror or e}"
            except Exception as e:  # noqa: BLE001 - never an exception in a caller: they keep the failure in memory
                return f"{d}: {type(e).__name__}: {e}"
            finally:
                try:
                    import fcntl
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            os.close(lock_fd)
    finally:
        os.close(dir_fd)


def _entry_for(doc: dict, info: dict) -> dict:
    """The record for one binary, created if absent, identity fields refreshed."""
    sha = info["sha256"]
    e = doc["binaries"].get(sha)
    if not isinstance(e, dict):
        e = {"last_probe": None, "failure": None, "last_pass_at": None}
        doc["binaries"][sha] = e
    e["module"] = info.get("module") or FROZEN_MODULE
    e["native_path"] = info.get("native_path")
    e["bytes"] = info.get("bytes")
    e["version"] = info.get("version") or "unknown"
    e.setdefault("last_probe", None)
    e.setdefault("failure", None)
    e.setdefault("last_pass_at", None)
    return e


def _activity_key(entry: dict) -> str:
    lp = entry.get("last_probe") or {}
    f = entry.get("failure") or {}
    return max(str(lp.get("at") or ""), str(f.get("last_at") or ""), str(entry.get("last_pass_at") or ""))


def _evict(doc: dict, keep: str) -> None:
    bins = doc["binaries"]
    while len(bins) > MAX_BINARIES:
        victims = sorted((k for k in bins if k != keep), key=lambda k: _activity_key(bins[k]))
        if not victims:
            return
        del bins[victims[0]]


def _new_failure(source: str, consumer: str, lab: str, err: str, now: float) -> dict:
    iso = _iso(now)
    return {"since": iso, "since_ts": now, "last_at": iso, "last_ts": now, "source": source,
            "consumer": consumer, "lab": lab, "error": err, "last_error": err, "count": 1}


def _landed_during_probe(f: dict, started_ts: float, now: float) -> bool:
    """Could failure ``f`` have been recorded while a probe that began at ``started_ts`` was running?

    Only if its stamp is a time between the probe's start and now. A stamp later than now (the wall clock
    was stepped back after the failure, as NTP or a VM resume can do) or not a finite number (a hand edit:
    every comparison with NaN is false, and infinity is later than any now) cannot belong to this probe,
    and leaving it would lock deep as failing until the clock caught up, which for NaN is never.
    ``CLOCK_SLACK_S`` forgives the few milliseconds two processes' clocks can disagree by. TD16's count
    snapshot would also cover a clock stepped back *during* a probe; this is the smaller rule, and it needs
    no new field in the probe's result."""
    try:
        last = float(f.get("last_ts") or 0.0)
    except (TypeError, ValueError):
        return False
    return started_ts <= last <= now + CLOCK_SLACK_S


def _bump_failure(f: dict, err: str, now: float) -> None:
    f["last_at"] = _iso(now)
    f["last_ts"] = now
    f["last_error"] = err
    f["count"] = int(f.get("count") or 0) + 1


# ---------------------------------------------------------------------------
# Writers (C1): note_call, note_load_failure, record_probe
# ---------------------------------------------------------------------------
def counters() -> dict[str, int]:
    """In-process call counters. They explain nothing and clear nothing."""
    return {"ok": _P.ok, "failed": _P.failed}


def _mem_note(key: str, source: str, consumer: str, lab: str, err: str, now: float) -> bool:
    """Record a process-local failure. True when it is a new transition."""
    f = _P.mem.get(key)
    _P.mem_version += 1
    if f is None:
        _P.mem[key] = _new_failure(source, consumer, lab, err, now)
        return True
    _bump_failure(f, err, now)
    return False


def _binary_info(b: Binary) -> dict:
    return {"sha256": b.sha256, "module": b.module, "native_path": b.native_path,
            "bytes": b.bytes, "version": b.version}


def _note_failure(source: str, error: Any, consumer: Optional[str], persist: bool) -> None:
    b = identity(import_if_needed=False)
    key = b.sha256 if b is not None else UNIDENTIFIED
    err = _clip(_err_text(error), 300)
    consumer = _clip(consumer, 80) if consumer else _default_consumer()
    lab = _lab()
    now = _now()
    child = _is_probe_child()
    transition = False
    persisted = False
    with _lock:
        if persist and not child and b is not None:
            flag = {"transition": False}

            def mutate(doc: dict) -> None:
                entry = _entry_for(doc, _binary_info(b))
                f = entry.get("failure")
                if f is None:
                    entry["failure"] = _new_failure(source, consumer, lab, err, now)
                    flag["transition"] = True
                else:
                    _bump_failure(f, err, now)
                _evict(doc, keep=b.sha256)

            problem = _locked_update(mutate)
            if problem is None:
                persisted = True
                transition = flag["transition"]
                _P.write_error = None
                _P.mem.pop(key, None)
            else:
                _P.write_error = problem
        if not persisted:
            is_new = _mem_note(key, source, consumer, lab, err, now)
            # A failure already persisted for this binary is not a new transition.
            transition = is_new and not _persisted_failure(key)
        _P.verdict_cache = None
    if transition and not child:
        _emit_transition(source, persist and not (b is None), consumer, lab, err, b)


def _persisted_failure(key: str) -> Optional[dict]:
    doc, _err = _read_store()
    if not doc:
        return None
    e = doc["binaries"].get(key)
    return e.get("failure") if isinstance(e, dict) else None


def _emit_transition(source: str, persisted: bool, consumer: str, lab: str, err: str,
                     b: Optional[Binary]) -> None:
    sha = f" (sha256 {b.sha256[:12]})" if b is not None else ""
    err160 = _clip(err, 160)
    if source == "load" and not persisted:
        msg = (f"Deep model failed to load in {consumer} ({lab}): {err160}. "
               "Deep generation is marked failing in this process only; fix the cause and restart the lab.")
    else:
        what = "failed to load" if source == "load" else "failed"
        msg = (f"Deep model {what} in {consumer} ({lab}): {err160}. "
               f"Deep generation is now marked failing for this build{sha}; agents and the Curator use "
               "the fast model until ./arailctl deep probe passes 5 of 5.")
    _emit("warn", msg)


def note_call(ok: bool, *, error: object = None, consumer: Optional[str] = None) -> None:
    """Record the outcome of one deep call. Never raises.

    A success updates counters and clears nothing. A failure marks the binary
    failing (persisted), unless this is the probe child, which keeps it in
    memory because the supervisor is the only writer.
    """
    try:
        if ok:
            _P.ok += 1
            return
        _P.failed += 1
        _note_failure("call", error, consumer, persist=True)
    except Exception:  # noqa: BLE001 - the verdict must never break a deep call
        pass


def note_load_failure(error: object, *, persist: bool, consumer: Optional[str] = None) -> None:
    """Record that the runtime would not load. ``persist=False`` keeps it in this process (a model-dir
    fault says nothing about the binary). Never raises."""
    try:
        _P.failed += 1
        _note_failure("load", error, consumer, persist=persist)
    except Exception:  # noqa: BLE001
        pass


def record_probe(result: dict) -> tuple[bool, Optional[str]]:
    """Store one probe result. Returns ``(written, error)``. Never raises.

    ``result`` keys: ``binary`` {sha256, module, native_path, bytes, version};
    ``outcome`` ("pass" | "fail"); ``passed``; ``total``; ``median_ok_s``;
    ``first_error`` {call, kind, error} or None; ``started_ts`` (float);
    ``started_at``; optional ``at``; ``model``; ``ring_depth``;
    ``ring_depth_source``; ``max_tokens``; ``prompt_set_sha256``; ``python``;
    ``lab``. A pass clears the binary's failure unless that failure could have
    landed while the probe ran (see ``_landed_during_probe``): one last seen before
    ``started_ts`` is cleared, and so is one stamped in the future or not a time.
    """
    try:
        if not isinstance(result, dict):
            return False, "the probe result is not a mapping"
        info = result.get("binary")
        if not isinstance(info, dict) or len(str(info.get("sha256") or "")) != 64:
            return False, "the probe result names no binary sha256"
        outcome = result.get("outcome")
        if outcome not in ("pass", "fail"):
            return False, f"unknown probe outcome {outcome!r}"
        now = _now()
        at = str(result.get("at") or _iso(now))
        started_ts = float(result.get("started_ts") or now)
        fe = result.get("first_error")
        if isinstance(fe, dict):
            fe = {"call": fe.get("call"), "kind": fe.get("kind"), "error": _clip(fe.get("error"), 300)}
        else:
            fe = None
        lp = {
            "started_at": str(result.get("started_at") or _iso(started_ts)),
            "started_ts": started_ts, "at": at, "outcome": outcome,
            "passed": result.get("passed"), "total": result.get("total"),
            "median_ok_s": result.get("median_ok_s"), "first_error": fe,
            "model": result.get("model"), "ring_depth": result.get("ring_depth"),
            "ring_depth_source": result.get("ring_depth_source"),
            "max_tokens": result.get("max_tokens"),
            "prompt_set_sha256": result.get("prompt_set_sha256"),
            "python": result.get("python"), "lab": result.get("lab") or _lab(),
        }
        sha = info["sha256"]

        def mutate(doc: dict) -> None:
            entry = _entry_for(doc, info)
            entry["last_probe"] = lp
            f = entry.get("failure")
            if outcome == "pass":
                if f is None:
                    entry["last_pass_at"] = at
                elif not _landed_during_probe(f, started_ts, _now()):
                    entry["failure"] = None
                    entry["last_pass_at"] = at
                # else: a failure landed while the probe ran; it survives the pass
            else:
                err = (fe or {}).get("error") or "the probe failed"
                if f is None:
                    entry["failure"] = _new_failure("probe", "deep-probe", lp["lab"], err, now)
                else:
                    _bump_failure(f, err, now)
            _evict(doc, keep=sha)

        with _lock:
            problem = _locked_update(mutate)
            _P.verdict_cache = None
        if problem is not None:
            _P.write_error = problem
            return False, problem
        _P.write_error = None
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def record_for(sha256: str) -> Optional[dict]:
    """The stored record of one binary (a copy), or None. Read only. Never raises."""
    try:
        doc, _err = _read_store()
        e = (doc or {}).get("binaries", {}).get(sha256)
        return json.loads(json.dumps(e)) if isinstance(e, dict) else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Readers (C1): current, is_failing, and the user-facing text (C8)
# ---------------------------------------------------------------------------
def _what_failed(f: dict, lp: Optional[dict]) -> str:
    source = f.get("source")
    lab, consumer = f.get("lab") or "root", f.get("consumer") or "unattributed"
    err = _clip(f.get("last_error") or f.get("error"), 120) or "no error text was recorded"
    if source == "probe":
        fe = (lp or {}).get("first_error") or {}
        if lp and lp.get("outcome") == "fail":
            where = f", first error at call {fe['call']}" if fe.get("call") else ""
            return (f"last probe {lp.get('passed')}/{lp.get('total')}{where}: "
                    f"{_clip(fe.get('error') or err, 120)}")
        return f"the last probe failed: {err}"
    if source == "load":
        return f"the deep model failed to load in {lab} ({consumer}): {err}"
    return f"a deep call failed in {lab} ({consumer}): {err}"


def what_failed(v: Verdict) -> str:
    """The short phrase for *why* deep is failing, without the surrounding sentence:
    ``last probe 1/5, first error at call 2: ...``, ``a deep call failed in ai
    (agent:buddy): ...``, or ``the verdict file is unreadable (...)``. Empty when not failing."""
    if v.state != "failing":
        return ""
    if v.source == "store_unreadable":
        return f"the verdict file is unreadable ({v.store_error})"
    return _what_failed(v.failure or {}, v.last_probe)


def _failing_detail(b: Optional[Binary], f: dict, lp: Optional[dict], changed: bool) -> str:
    who = (f"QueueLLM {b.version}, sha256 {b.sha256[:12]}" if b is not None else "binary not identified")
    since = f" — since {_utc_minute(f.get('since'))}" if f.get("since") else ""
    text = (f"deep generation is failing on this build ({who}): {_what_failed(f, lp)}{since}. "
            "Agents and the Curator use the fast model until ./arailctl deep probe passes 5/5 on this build.")
    if changed:
        text += " The binary on disk changed after this process loaded it: restart the lab to load the new build."
    return text


def _passed_since(f: dict, lp: Any) -> bool:
    """Did a stored passing probe start after failure ``f`` was last seen?

    Strictly after: a failure stamped at the probe's start or later may have landed while it ran, and it
    stands. With no usable stamp on either side the answer is no (the failure stands)."""
    if not isinstance(lp, dict) or lp.get("outcome") != "pass":
        return False
    try:
        return float(f["last_ts"]) < float(lp["started_ts"])
    except (KeyError, TypeError, ValueError):
        return False


def passed_after(v: "Verdict", stamp: Any) -> bool:
    """True when ``v`` rests on a stored passing probe that started strictly after ``stamp`` (an epoch time).

    The rule ``record_probe`` applies to a stored failure and ``_assemble`` to an in-memory one, for a caller
    that keeps a record of its own (the model registry's stored health): what was recorded before a passing
    probe started is superseded by it; what was recorded during or after the probe stands. A verdict that is
    not ``passing`` supersedes nothing."""
    return v.state == "passing" and _passed_since({"last_ts": stamp}, v.last_probe)


def _assemble(b: Optional[Binary], doc: Optional[dict], store_err: Optional[str], changed: bool) -> Verdict:
    write_err = _P.write_error
    if store_err is not None:
        detail = (f"deep generation is failing on this build"
                  f"{f' (QueueLLM {b.version}, sha256 {b.sha256[:12]})' if b is not None else ''}: "
                  f"the verdict file is unreadable ({store_err}). "
                  "Agents and the Curator use the fast model until ./arailctl deep probe passes 5/5 on this build.")
        if changed:
            detail += " The binary on disk changed after this process loaded it: restart the lab to load the new build."
        return Verdict("failing", b, detail, None, "store_unreadable", None, None, changed, store_err)

    key = b.sha256 if b is not None else UNIDENTIFIED
    entry = ((doc or {}).get("binaries") or {}).get(key) if b is not None else None
    persisted_f = entry.get("failure") if isinstance(entry, dict) else None
    mem_f = _P.mem.get(key)
    lp = copy.deepcopy(entry.get("last_probe")) if isinstance(entry, dict) else None
    if mem_f is not None and _passed_since(mem_f, lp):
        # This lab could not save that failure (F12) and kept it here. A probe, which always runs in another
        # process and so cannot reach this copy, has since passed on this binary: the read applies the same
        # rule the probe's write applies, and with the failure gone there is nothing left unsaved to report.
        mem_f, write_err = None, None
    # "failure is not null" means failing (C2), so an empty object is a failure too.
    f = copy.deepcopy(persisted_f) if persisted_f is not None else None
    f = f if f is not None else mem_f
    if persisted_f is not None and mem_f is not None \
            and float(mem_f.get("last_ts") or 0) > float(persisted_f.get("last_ts") or 0):
        f = mem_f
    if f is not None:
        detail = _failing_detail(b, f, lp, changed)
        return Verdict("failing", b, detail, f.get("since"), f.get("source"), copy.deepcopy(f), lp, changed, write_err)
    if b is None:
        why = _P.ident_reason or "no native QueueLLM module is loaded"
        return Verdict("untested", None,
                       f"deep generation has not been tested: the loaded QueueLLM binary could not be "
                       f"identified ({why}) — run ./arailctl deep probe.",
                       None, None, None, None, changed, write_err)
    if isinstance(lp, dict) and lp.get("outcome") == "pass":
        med = lp.get("median_ok_s")
        med_txt = f", median {float(med):.1f} s" if isinstance(med, (int, float)) else ""
        detail = (f"deep generation passed {lp.get('passed')} of {lp.get('total')} sequential calls on this "
                  f"build (QueueLLM {b.version}, sha256 {b.sha256[:12]}) at {_utc_minute(lp.get('at'))}{med_txt}.")
        if changed:
            detail += " The binary on disk changed after this process loaded it: restart the lab to load the new build."
        return Verdict("passing", b, detail, lp.get("at"), "probe", None, lp, changed, write_err)
    detail = (f"deep generation has not been tested on this build (QueueLLM {b.version}, sha256 "
              f"{b.sha256[:12]}); an import is not a generation test — run ./arailctl deep probe.")
    if changed:
        detail += " The binary on disk changed after this process loaded it: restart the lab to load the new build."
    return Verdict("untested", b, detail, None, None, None, lp if isinstance(lp, dict) else None, changed, write_err)


def current(*, import_if_needed: bool = True) -> Verdict:
    """The verdict for the binary this process loaded. Never generates, never raises."""
    try:
        with _lock:
            b = identity(import_if_needed=import_if_needed)
            doc, store_err = _read_store()
            changed = _changed_on_disk()
            sig = _P.read_cache[1] if _P.read_cache else None
            ck = (b.sha256 if b else None, str(_store_file()), sig, store_err, _P.mem_version,
                  _P.write_error, changed, _P.ident_reason if b is None else "")
            cached = _P.verdict_cache
            if cached is not None and cached[0] == ck:
                return cached[1]
            v = _assemble(b, doc, store_err, changed)
            _P.verdict_cache = (ck, v)
            return v
    except Exception as e:  # noqa: BLE001
        return Verdict("untested", None,
                       f"deep generation has not been tested: the verdict could not be read "
                       f"({type(e).__name__}: {e}) — run ./arailctl deep probe.",
                       None, None, None, None, False, f"{type(e).__name__}: {e}")


def is_failing() -> bool:
    """True when ``current(import_if_needed=False)`` says failing. Cheap, never raises."""
    try:
        return current(import_if_needed=False).state == "failing"
    except Exception:  # noqa: BLE001
        return False


def health_detail(v: Verdict) -> str:
    """The tier-1 health text while failing (C5). Empty when not failing.

    It starts with ``HEALTH_PREFIX`` so a stored copy can be recognised later.
    """
    if v.state != "failing":
        return ""
    sha = f" (sha256 {v.binary.sha256[:12]})" if v.binary is not None else ""
    f = v.failure or {}
    if v.source == "store_unreadable":
        what = f"the verdict file is unreadable ({_clip(v.store_error, 120)})"
        since = ""
    else:
        fe = ((v.last_probe or {}).get("first_error") or {}) if v.source == "probe" else {}
        what = _clip(fe.get("error") or f.get("last_error") or f.get("error") or "generation failed", 120)
        since = f" — since {_utc_minute(f.get('since'))}" if f.get("since") else ""
    return (f"{HEALTH_PREFIX}{sha}: {what}{since}. "
            "Check re-reads this; only ./arailctl deep probe can clear it.")


# ---------------------------------------------------------------------------
# Provenance and status text (C7). Used by ``python -m arail.deep_verdict`` and
# by ``scripts/build-aerollm.sh status``. Everything here is read only.
# ---------------------------------------------------------------------------
def teach_line(key: str, value: str, table: Optional[dict] = None) -> str:
    """One line that carries all four parts for one number: meaning, bar and why,
    what out of range means, where the failure lives."""
    t = (table or TEACHING)[key]
    return (f"{value}: {t['meaning']}. Bar: {t['threshold']}. "
            f"Out of range: {t['out_of_range']}. Fault lives in: {t['failure_lives_in']}.")


def shadowed(native_path: Optional[str]) -> list[dict]:
    """Other native files for the frozen import that sit on ``sys.path`` and lose to the loaded one.

    ``[{"path", "sha256", "mtime"}]``. A top-level extension next to a package of
    the same name is the live case: the package wins and the extension is never loaded.
    """
    out: list[dict] = []
    try:
        loaded = os.path.realpath(native_path) if native_path else None
        seen: set[str] = set()
        for entry in list(sys.path):
            d = entry or os.getcwd()
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                if n.split(".", 1)[0] not in NATIVE_MODULE_NAMES:
                    continue
                p = os.path.join(d, n)
                cands: list[str] = []
                if n.endswith(_EXT_SUFFIXES) and os.path.isfile(p):
                    cands.append(p)
                elif n in NATIVE_MODULE_NAMES and os.path.isdir(p):
                    try:
                        cands += [os.path.join(p, m) for m in sorted(os.listdir(p)) if m.endswith(_EXT_SUFFIXES)]
                    except OSError:
                        pass
                for c in cands:
                    rp = os.path.realpath(c)
                    if rp == loaded or rp in seen:
                        continue
                    seen.add(rp)
                    out.append({"path": c, "sha256": _sha256_file(c), "mtime": _iso(os.stat(c).st_mtime)})
    except Exception:  # noqa: BLE001
        pass
    return out


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001 - unreadable, not JSON, or nested too deeply (RecursionError): no marker
        return None


def _record_hash_hex(b64: str) -> str:
    import base64
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).hex()


def _pip_record(native_path: str, site_dir: Path) -> Optional[dict]:
    """The pip dist that claims the native file, if any: name, version, editable url,
    RECORD hash (hex) of that file, or None when no dist is installed."""
    try:
        from importlib import metadata
        dist = None
        for name in ("aerollm-api", "aerollm_api"):
            found = list(metadata.Distribution.discover(name=name, path=[str(site_dir)]))
            if found:
                dist = found[0]
                break
        if dist is None:
            return None
        info: dict = {"name": "aerollm-api", "version": dist.version, "editable_url": None, "record_sha256": None}
        raw = dist.read_text("direct_url.json")
        if raw:
            try:
                du = json.loads(raw)
                if (du.get("dir_info") or {}).get("editable"):
                    info["editable_url"] = du.get("url")
            except Exception:  # noqa: BLE001 - not JSON, or nested too deeply (RecursionError)
                pass
        rel = os.path.relpath(os.path.realpath(native_path), os.path.realpath(str(site_dir)))
        for f in dist.files or []:
            if str(f) == rel and f.hash is not None and f.hash.mode == "sha256":
                info["record_sha256"] = _record_hash_hex(f.hash.value)
                break
        return info
    except Exception:  # noqa: BLE001
        return None


def channel(b: Binary, site_dir: Path) -> str:
    """How this binary got here, only when it can be proven; else ``unknown — <reason>``.

    bundled: the bundle marker's sha256 equals the loaded file's.
    source build: ``aerollm_api.build.json``'s artifact_sha256 equals it.
    pip: the dist's RECORD hash for the file equals it.
    """
    reasons: list[str] = []
    bundle = _read_json(site_dir / "aerollm_api.bundle.json")
    if bundle is not None:
        if bundle.get("sha256") == b.sha256:
            sha7 = str(bundle.get("aerollm_commit") or "?")[:7]
            return (f"bundled — QueueLLM {bundle.get('aerollm_version', 'unknown')} "
                    f"({sha7}, built {bundle.get('built_at', 'unknown')})")
        reasons.append(f"a bundle marker is present but names a different binary "
                       f"(marker {str(bundle.get('sha256'))[:12]}…, file {b.sha256[:12]}…)")
    build = _read_json(site_dir / "aerollm_api.build.json")
    if build is not None:
        if build.get("artifact_sha256") == b.sha256:
            state = build.get("queuellm_dirty")
            dirty = ", dirty tree" if state is True else (", tree state unknown" if state is None else "")
            return (f"source build — QueueLLM commit {str(build.get('queuellm_commit') or '?')[:7]}{dirty}, "
                    f"built {build.get('built_at', 'unknown')}")
        reasons.append(f"a source-build marker is present but names a different binary "
                       f"(marker {str(build.get('artifact_sha256'))[:12]}…, file {b.sha256[:12]}…)")
    pip = _pip_record(b.native_path, site_dir)
    if pip is not None:
        where = f"editable, {pip['editable_url']}" if pip["editable_url"] else "regular install"
        if pip["record_sha256"] == b.sha256:
            return f"pip ({pip['name']} {pip['version']}, {where})"
        if pip["record_sha256"] is None:
            reasons.append(f"pip package {pip['name']} {pip['version']} ({where}) does not list this file")
        else:
            reasons.append(f"pip package {pip['name']} {pip['version']} ({where}), but its binary was "
                           f"replaced outside pip (RECORD {pip['record_sha256'][:12]}…, file {b.sha256[:12]}…)")
    if not reasons:
        reasons.append("no install record")
    return "unknown — " + "; ".join(reasons)


def _owner_of(path: str) -> str:
    """Who put *path* on the import path, as far as can be proven: a pip dist whose RECORD lists it,
    a PYTHONPATH entry, or nothing."""
    real = os.path.realpath(path)
    try:
        from importlib import metadata
        for name in ("aerollm-api", "aerollm_api"):
            for dist in metadata.Distribution.discover(name=name):
                for f in dist.files or []:
                    try:
                        hit = os.path.realpath(str(dist.locate_file(f))) == real
                    except Exception:  # noqa: BLE001
                        hit = False
                    if hit:
                        text = f"pip package {dist.metadata['Name']} {dist.version}"
                        raw = dist.read_text("direct_url.json")
                        try:
                            du = json.loads(raw) if raw else {}
                        except Exception:  # noqa: BLE001 - not JSON, or nested too deeply (RecursionError)
                            du = {}
                        if (du.get("dir_info") or {}).get("editable") and du.get("url"):
                            text += f", editable from {du['url']}"
                        return text
    except Exception:  # noqa: BLE001
        pass
    for entry in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        if entry and (real + os.sep).startswith(os.path.realpath(entry) + os.sep):
            return "a PYTHONPATH entry"
    return "no install record"


def import_target(dest: str) -> dict:
    """Would a file copied to *dest* be the one ``import aerollm_api`` loads? Nothing is imported.

    ``{"free": True}`` when the import finds nothing yet, or already resolves to *dest*;
    otherwise ``{"free": False, "winner": <path>, "owner": <who put it there>}``: a package
    directory or another path entry wins, and a copy at *dest* would never load.
    """
    spec = _find_spec_safe()
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin:
        return {"free": True, "winner": None, "owner": ""}
    if os.path.realpath(origin) == os.path.realpath(dest):
        return {"free": True, "winner": origin, "owner": ""}
    return {"free": False, "winner": origin, "owner": _owner_of(origin)}


def _generation_lines(v: Verdict) -> list[str]:
    """The ``generation:`` text. Every number in it is followed by a line that teaches it."""
    f = v.failure or {}
    lp = v.last_probe or {}
    lines: list[str] = []
    if v.state == "untested":
        lines.append("never tested on this binary — run ./arailctl deep probe (an import is not a generation test)")
    elif v.state == "passing":
        med = lp.get("median_ok_s")
        med_txt = f", median {float(med):.1f} s" if isinstance(med, (int, float)) else ""
        lines.append(f"passing — {lp.get('passed')}/{lp.get('total')} at the last probe "
                     f"({_utc_minute(lp.get('at'))}){med_txt}")
        lines.append(teach_line("passed", f"{lp.get('passed')}/{lp.get('total')}"))
        if med_txt:
            lines.append(teach_line("median_ok_s", f"{float(med):.1f} s"))
    else:
        if v.source == "probe" and lp.get("outcome") == "fail":
            fe = lp.get("first_error") or {}
            at_call = f"; first error at call {fe['call']}" if fe.get("call") else ""
            lines.append(f"FAILING — {lp.get('passed')}/{lp.get('total')} at the last probe "
                         f"({_utc_minute(lp.get('at'))}){at_call}: {_clip(fe.get('error') or f.get('error'), 160)}")
            lines.append(teach_line("passed", f"{lp.get('passed')}/{lp.get('total')}"))
        elif v.source == "store_unreadable":
            lines.append(f"FAILING — the verdict file is unreadable ({v.store_error}); deep stays marked failing "
                         "until ./arailctl deep probe writes a fresh one")
        elif v.source == "load":
            lines.append(f"FAILING — the deep model failed to load in lab '{f.get('lab')}' ({f.get('consumer')}) "
                         f"at {_utc_minute(f.get('since'))}: {_clip(f.get('last_error') or f.get('error'), 160)}")
        else:
            lines.append(f"FAILING — a deep call failed in lab '{f.get('lab')}' ({f.get('consumer')}) "
                         f"at {_utc_minute(f.get('since'))}: {_clip(f.get('last_error') or f.get('error'), 160)}")
        if f.get("count") is not None:
            lines.append(teach_line("failures", str(f.get("count"))))
        lines.append("Only a ./arailctl deep probe that passes 5/5 on this binary clears this.")
    if v.store_error and v.source != "store_unreadable":
        lines.append(f"NOT SAVED ({v.store_error}): other labs and the next process will not see this result")
    if v.binary_changed_on_disk:
        lines.append("The binary on disk changed after this process loaded it: restart the lab to load the new build.")
    return lines


def status_lines(*, site_dir: Optional[Path] = None, quiet_import: bool = True) -> list[str]:
    """The ``import / binary / sha256 / shadowed / channel / generation`` block of ``deep status``.

    Four-space indent and a 14-column label, to sit under the build script's own lines.
    Imports the frozen module (a status check always did), with stdout parked on stderr.

    Every line is run through ``_printable`` here, at the one place the block leaves this module: besides
    error text it carries fields no ``_clip`` touches (the lab and consumer names, timestamps, paths, the
    version and channel text), and a verdict file anyone with write access can edit supplies all of them.
    """
    return [_printable(line) for line in _status_lines(site_dir=site_dir, quiet_import=quiet_import)]


def _status_lines(*, site_dir: Optional[Path], quiet_import: bool) -> list[str]:
    def row(label: str, text: str) -> str:
        return f"    {label + ':':<14}{text}"

    cont = " " * 18
    out: list[str] = []
    py = sys.executable or "python"
    if FROZEN_MODULE not in sys.modules and _find_spec_safe() is None:
        out.append(row("import", f"{FROZEN_MODULE} is not importable by {py} — run: ./arailctl deep install "
                                 "(maintainers: deep rebuild needs a pre-rename checkout; "
                                 "deep update needs the private index)"))
        out.append(row("channel", "none"))
        out.append(row("generation", "not applicable — nothing is installed"))
        return out
    with _stdout_to_stderr(quiet_import):
        b = identity(import_if_needed=True)
    if b is None:
        out.append(row("import", f"{FROZEN_MODULE} -> {_P.ident_reason or 'could not be identified'}"))
        out.append(row("channel", "unknown — the loaded binary could not be identified"))
        out.append(row("generation", "unknown — the loaded binary could not be identified; "
                                     "run ./arailctl deep probe"))
        return out
    kind = ("package" if b.resolves_to.endswith("__init__.py")
            else "extension" if b.resolves_to == b.native_path else "module")
    out.append(row("import", f"{FROZEN_MODULE} -> {b.resolves_to} ({kind})"))
    out.append(row("binary", b.native_path))
    out.append(row("sha256", f"{b.sha256[:12]}… · {b.bytes} bytes · modified {b.mtime[:10]} · version {b.version}"))
    sh = shadowed(b.native_path)
    if not sh:
        out.append(row("shadowed", "none"))
    for i, s in enumerate(sh):
        out.append(row("shadowed" if i == 0 else "",
                       f"{s['path']} · sha256 {s['sha256'][:12]}… · modified {s['mtime'][:10]} · "
                       "on the path, never loaded"))
    site = site_dir if site_dir is not None else Path(_platlib())
    out.append(row("channel", channel(b, site)))
    gen = _generation_lines(current(import_if_needed=False))
    out.append(row("generation", gen[0]))
    out.extend(cont + line for line in gen[1:])
    return out


def _platlib() -> str:
    import sysconfig
    return sysconfig.get_path("platlib")


class _stdout_to_stderr:
    """Park fd 1 on fd 2 so native code that prints on import cannot corrupt stdout."""

    def __init__(self, active: bool = True) -> None:
        self.active = active
        self.saved: Optional[int] = None

    def __enter__(self) -> "_stdout_to_stderr":
        if self.active:
            try:
                sys.stdout.flush()
                self.saved = os.dup(1)
                os.dup2(2, 1)
            except OSError:
                self.saved = None
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.saved is not None:
            try:
                sys.stdout.flush()
                os.dup2(self.saved, 1)
            finally:
                os.close(self.saved)


_USAGE = """usage: python -m arail.deep_verdict status
       python -m arail.deep_verdict identity --json
       python -m arail.deep_verdict target <dest>
       python -m arail.deep_verdict channel

status    the import / binary / sha256 / shadowed / channel / generation lines of `deep status`
identity  the binary this interpreter loads for `import aerollm_api`, as JSON (exit 1 if none)
target    would a file copied to <dest> be the one `import aerollm_api` loads? JSON; imports nothing
channel   how the loaded binary got here, only when it can be proven (else `unknown - <reason>`)
"""


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(_USAGE)
        return 2
    if args[0] in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        return 0
    if args[0] == "status" and len(args) == 1:
        for line in status_lines():
            print(line)
        return 0
    if args[0] == "target" and len(args) == 2:
        print(json.dumps(import_target(args[1])))
        return 0
    if args[0] == "channel" and len(args) == 1:
        with _stdout_to_stderr():
            b = identity(import_if_needed=True)
        print(_printable(channel(b, Path(_platlib())) if b is not None
                         else "unknown — the loaded binary could not be identified"))
        return 0
    if args[0] == "identity" and args[1:] == ["--json"]:
        with _stdout_to_stderr():
            b = identity(import_if_needed=True)
        if b is None:
            print(json.dumps({"error": _P.ident_reason or "the loaded binary could not be identified"}))
            return 1
        print(json.dumps({"module": b.module, "resolves_to": b.resolves_to, "native_path": b.native_path,
                          "sha256": b.sha256, "bytes": b.bytes, "mtime": b.mtime, "version": b.version}))
        return 0
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(main())
