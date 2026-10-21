"""Bridge between ARAIL and the opt-in Mastra Studio spike (maximus only).

ARAIL stays the source of truth. Node (Mastra) never writes ARAIL files: this
module spawns ``run-once.mjs``, validates its single JSON line, and then writes
the score through ``ExperimentTracker.observe``.

Sprint: sprints/2026-10-13-mastra-studio-spike/ARCHITECTURE.md (C3, C4).
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
MASTRA_DIR = REPO_ROOT / "integrations" / "mastra-studio"
STUDIO_HOST = "127.0.0.1"
MIN_NODE = (22, 13, 0)
MASTRA_VERSION = "1.34.0"
CORE_VERSION = "1.76.0"
SCORER_ID = "arail-probe-report-grounded-v0"
OBSERVATION_TEXT = "mastra probe score (not a 3DEval measurement)"
RUN_TIMEOUT_S = 15 * 60
MAX_CHILD_STDOUT = 64 * 1024
MAX_ID_LEN = 128
_EXP_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The three flags that keep Mastra airgapped. Always forced, never inherited.
REQUIRED_FLAGS: Dict[str, str] = {
    "MASTRA_TELEMETRY_DISABLED": "1",
    "MASTRA_OFFLINE": "1",
    "MASTRA_AUTO_REFRESH_PROVIDERS": "false",
}

_ALLOW_EXACT = {
    "PATH", "HOME", "ARAIL_MASTRA_DB", "ARAIL_DATA_DIR", "ARAIL_EXPERIMENTS_DIR",
    "LAB_TIER", "LAB_MODE", "OLLAMA_HOST", "ARAIL_OLLAMA_URL", "ARAIL_PYTHON",
    "ARAIL_PROBE_MODEL", "PYTHONPATH", "TMPDIR", "LAB_ROOT", "LAB_PKB", "ARAIL_ROOT",
}
# Dropped even if an allowlisted name matches (e.g. NODE_AUTH_TOKEN).
_DENY = re.compile(r"(_API_KEY|_TOKEN|LICENSE_KEY|SECRET|PASSWORD)$|^MASTRA_(PLATFORM|LICENSE|EE_)|^MASTRA_EE", re.I)


def mastra_db_path() -> Path:
    """Absolute: the Node child runs from integrations/mastra-studio, not the lab cwd."""
    from arail.config import DATA_DIR
    return Path(DATA_DIR).resolve() / "mastra" / "mastra.db"


def mastra_env(parent: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Child environment built from scratch from an allowlist (F2, F3, EE guard)."""
    src = os.environ if parent is None else parent
    env: Dict[str, str] = {}
    for k, v in src.items():
        if k in _ALLOW_EXACT and not _DENY.search(k):
            env[k] = v
    from arail.config import DATA_DIR
    # ARAIL resolves lab paths relative to its cwd; the Mastra project dir is not the lab,
    # so pass absolute paths and the cwd the MCP server must use.
    from arail.config import LAB_ROOT, PKB_ROOT
    # Always absolute and derived from ARAIL's own config (never a relative value inherited
    # from the parent env): run-once.mjs requires the DB under the data dir by absolute path.
    env["ARAIL_ROOT"] = os.getcwd()
    env["LAB_ROOT"] = str(Path(LAB_ROOT).resolve())
    env["LAB_PKB"] = str(Path(PKB_ROOT).resolve())
    env["ARAIL_DATA_DIR"] = str(Path(DATA_DIR).resolve())
    env["ARAIL_MASTRA_DB"] = str(mastra_db_path())
    env.setdefault("ARAIL_PYTHON", sys.executable)
    env.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
    env.setdefault("LAB_TIER", "maximus")
    env.update(REQUIRED_FLAGS)
    return env


# ── Node / install / page state ───────────────────────────────────────

def node_version() -> Optional[tuple]:
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", out.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_installed() -> bool:
    return (MASTRA_DIR / "node_modules" / "mastra").is_dir()


STUDIO_PORT = 4111  # fixed: the Studio port is not configurable in this spike


def studio_port() -> int:
    return STUDIO_PORT


def port_open(port: int, host: str = STUDIO_HOST, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _under_mastra_dir(path: str) -> bool:
    try:
        Path(path).resolve().relative_to(MASTRA_DIR.resolve())
        return True
    except (ValueError, OSError):
        return False


def listener_pids(port: int) -> Optional[list]:
    """Pids listening on 127.0.0.1/::1:port (None when lsof is unavailable)."""
    lsof = shutil.which("lsof")
    if not lsof:
        return None
    try:
        out = subprocess.run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted({int(ln[1:]) for ln in out.splitlines() if ln.startswith("p") and ln[1:].isdigit()})


def _process_cwd(pid: int) -> str:
    lsof = shutil.which("lsof")
    if not lsof:
        return ""
    try:
        out = subprocess.run([lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return next((ln[1:] for ln in out.splitlines() if ln.startswith("n")), "")


def listener_state(port: Optional[int] = None) -> str:
    """'none' | 'ours' | 'foreign' for the Studio port. A listener is ours only if every
    listening process has its working directory under integrations/mastra-studio."""
    port = port or studio_port()
    pids = listener_pids(port)
    if pids is None:                       # cannot identify anyone: fall back to a plain connect
        return "foreign" if port_open(port) else "none"
    if not pids:
        return "foreign" if port_open(port) else "none"   # listening but not visible to lsof
    return "ours" if all(_under_mastra_dir(_process_cwd(p)) for p in pids) else "foreign"


def resolve_page_state(
    get_node: Callable[[], Optional[tuple]] = node_version,
    installed: Callable[[], bool] = is_installed,
    running: Callable[[], Any] = listener_state,
) -> Dict[str, str]:
    """Exactly one of node_missing | node_too_old | not_installed | port_in_use | not_running | running.

    ``running()`` returns 'none' | 'ours' | 'foreign' (a bool is accepted: True means ours)."""
    v = get_node()
    if v is None:
        return {"state": "node_missing", "detail": "Node.js was not found. Mastra Studio needs Node 22.13 or newer."}
    if v < MIN_NODE:
        found = ".".join(map(str, v))
        return {"state": "node_too_old", "detail": f"Found Node v{found}; Mastra Studio needs 22.13.0 or newer."}
    if not installed():
        return {"state": "not_installed", "detail": "Run ./arailctl mastra install to fetch the pinned packages."}
    ls = running()
    ls = ("ours" if ls else "none") if isinstance(ls, bool) else ls
    if ls == "foreign":
        return {"state": "port_in_use",
                "detail": f"Port {studio_port()} is in use by another program. ARAIL will not start, stop or open anything there."}
    if ls == "none":
        return {"state": "not_running", "detail": "Studio is installed but not running."}
    return {"state": "running", "detail": f"Studio is running on {STUDIO_HOST}:{studio_port()}."}


# ── Restricted browser (the supported way to open Studio) ────────────

BROWSER_RULES = "MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost"


def browser_profile_dir() -> Path:
    return mastra_db_path().parent / "browser"


def browser_args(url: Optional[str] = None) -> list:
    """Flags for a network-restricted Chromium: names other than loopback never resolve
    and any non-loopback connection is sent to a dead local proxy."""
    return [
        f"--user-data-dir={browser_profile_dir()}",
        f"--host-resolver-rules={BROWSER_RULES}",
        "--proxy-server=http://127.0.0.1:9",
        # WebRTC/STUN UDP to IP literals bypasses the proxy and the resolver rule.
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--proxy-bypass-list=127.0.0.1;localhost;[::1]",
        "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
        "--disable-component-update", "--disable-sync", "--no-pings", "--disable-default-apps",
        url or f"http://{STUDIO_HOST}:{studio_port()}/",
    ]


def find_browser() -> Optional[str]:
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if os.path.exists(p):
            return p
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        w = shutil.which(name)
        if w:
            return w
    return None


# ── Studio process lifecycle ──────────────────────────────────────────

def _pid_file() -> Path:
    return mastra_db_path().parent / "studio.pid"


def listener_addresses(port: int) -> Optional[list]:
    """LISTEN addresses for ``port`` via lsof, or None when it cannot be determined."""
    lsof = shutil.which("lsof")
    if not lsof:
        return None
    try:
        out = subprocess.run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fn"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [ln[1:] for ln in out.splitlines() if ln.startswith("n")]


def is_loopback_listener(addrs: list, port: int) -> bool:
    ok = {f"127.0.0.1:{port}", f"[::1]:{port}", f"localhost:{port}"}
    return bool(addrs) and all(a in ok for a in addrs)


def start_studio() -> Dict[str, Any]:
    st = resolve_page_state()
    if st["state"] in ("node_missing", "node_too_old", "not_installed", "port_in_use"):
        return {"ok": False, "error": st["state"], "status": 409}
    if st["state"] == "running":
        return {"ok": True, "already_running": True}
    try:
        mastra_db_path().parent.mkdir(parents=True, exist_ok=True)
        env = mastra_env()
        with open(mastra_db_path().parent / "studio.log", "ab") as log:
            proc = subprocess.Popen(
                ["npx", "--no-install", "mastra", "dev"], cwd=str(MASTRA_DIR), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, shell=False)
        # start_new_session makes the child its own group leader: pid == pgid.
        _pid_file().write_text(f"{proc.pid} {os.getpgid(proc.pid)}")
    except OSError:
        return {"ok": False, "error": "data_dir_not_writable", "status": 500}
    import time
    for _ in range(120):
        if port_open(studio_port()):
            break
        if proc.poll() is not None:
            return {"ok": False, "error": "studio_exited", "status": 500}
        time.sleep(0.5)
    else:
        stop_studio()
        return {"ok": False, "error": "studio_start_timeout", "status": 500}
    addrs = listener_addresses(studio_port())
    if addrs is not None and not is_loopback_listener(addrs, studio_port()):
        stop_studio()
        return {"ok": False, "error": "studio_not_loopback", "status": 500}
    return {"ok": True}


def _process_command(pid: int) -> str:
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# What the group leader Popen'd by start_studio looks like: `npm exec mastra dev` (or node/npx
# variants), run from integrations/mastra-studio. A bare "mastra" substring is not enough.
_LEADER_SHAPE = re.compile(r"^(\S*/)?(npm|npx|node)\s+(\S+\s+)*mastra\s+dev(\s|$)")


def pid_is_our_studio(pid: int, pgid: int, command_of: Callable[[int], str] = _process_command,
                      cwd_of: Callable[[int], str] = _process_cwd) -> bool:
    """True only for a live group leader (pid == pgid) with Studio's argv shape and cwd."""
    if pid != pgid:
        return False
    try:
        if os.getpgid(pid) != pgid:
            return False
    except (ProcessLookupError, PermissionError):
        return False
    return bool(_LEADER_SHAPE.match(command_of(pid))) and _under_mastra_dir(cwd_of(pid))


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _kill_group(pgid: int) -> None:
    import time
    try:
        os.killpg(pgid, signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.25)
            if not _group_alive(pgid):
                return
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop_studio(command_of: Callable[[int], str] = _process_command,
                cwd_of: Callable[[int], str] = _process_cwd) -> Dict[str, Any]:
    """Stop the Studio process group ARAIL started.

    A process group id cannot be reused while any member is alive, so if the recorded group
    is alive and its leader is dead, the survivors are Studio's own (orphaned) members. If
    the leader pid is alive it must look like Studio (argv shape + cwd), otherwise the pid
    was recycled and nothing is signalled. A listener on the Studio port that is ours but has
    no usable pid file is stopped via its own group; a foreign listener is never touched."""
    pf = _pid_file()
    stopped = False
    stale = False
    if pf.exists():
        try:
            pid_s, pgid_s = pf.read_text().split()
            pid, pgid = int(pid_s), int(pgid_s)
        except ValueError:
            pid = pgid = 0
        if pgid > 1 and pid == pgid and _group_alive(pgid):
            try:
                leader_alive = os.getpgid(pid) == pgid
            except ProcessLookupError:
                leader_alive = False
            except PermissionError:
                leader_alive = True
            if not leader_alive or pid_is_our_studio(pid, pgid, command_of, cwd_of):
                _kill_group(pgid)
                stopped = True
            else:
                stale = True
        else:
            stale = True
        pf.unlink(missing_ok=True)
    if listener_state() == "ours":
        for lp in listener_pids(studio_port()) or []:
            try:
                g = os.getpgid(lp)
            except ProcessLookupError:
                continue
            if g > 1 and g != os.getpgid(0):
                _kill_group(g)
                stopped = True
    out: Dict[str, Any] = {"ok": True, "was_running": stopped}
    if stale and not stopped:
        out["stale"] = True
    return out


# ── run-once: spawn, parse, validate, write back ──────────────────────

class ChildOutputError(ValueError):
    pass


def _is_id(v: Any) -> bool:
    return isinstance(v, str) and 0 < len(v) <= MAX_ID_LEN


def parse_child_output(stdout: Any) -> Dict[str, Any]:
    """Strictly parse run-once.mjs stdout: exactly one JSON line, validated fields."""
    raw = stdout.decode("utf-8", "replace") if isinstance(stdout, (bytes, bytearray)) else stdout
    if not isinstance(raw, str) or len(raw) > MAX_CHILD_STDOUT:
        raise ChildOutputError("bad_child_output")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) != 1:
        raise ChildOutputError("bad_child_output")
    try:
        obj = json.loads(lines[0], parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except ValueError:
        raise ChildOutputError("bad_child_output") from None
    if not isinstance(obj, dict):
        raise ChildOutputError("bad_child_output")
    if "error" in obj:
        code = obj["error"]
        raise ChildOutputError(code if isinstance(code, str) and len(code) <= MAX_ID_LEN else "bad_child_output")
    score = obj.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ChildOutputError("bad_child_output")
    for key in ("mastra_trace_id", "scorer_id", "mastra_version", "core_version"):
        if not _is_id(obj.get(key)):
            raise ChildOutputError("bad_child_output")
    for key in ("arail_trace_id", "experiment_id"):
        if obj.get(key) is not None and not _is_id(obj[key]):
            raise ChildOutputError("bad_child_output")
    return {**obj, "score": float(score)}


def probe_observation_data(parsed: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "mastra",
        "kind": "probe",
        "trace_id": parsed["mastra_trace_id"],
        "arail_trace_id": parsed.get("arail_trace_id"),
        "scorer_id": parsed["scorer_id"],
        "score": parsed["score"],
        "mastra_version": parsed["mastra_version"],
        "core_version": parsed["core_version"],
    }


def write_back(tracker: Any, exp_id: str, parsed: Dict[str, Any]) -> Dict[str, Any]:
    """The only ARAIL write of the Mastra path. Raises KeyError-style errors as codes."""
    try:
        tracker._load(exp_id)
    except Exception:  # noqa: BLE001 - missing/corrupt record
        return {"error": "experiment_missing"}
    tracker.observe(exp_id, OBSERVATION_TEXT, probe_observation_data(parsed))
    return {"experiment_id": exp_id, "mastra_trace_id": parsed["mastra_trace_id"], "score": parsed["score"]}


def model_available(base: Optional[str] = None, timeout: float = 2.0) -> bool:
    base = base or os.getenv("ARAIL_OLLAMA_URL") or "http://127.0.0.1:11434"
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def run_probe(goal: Any, experiment_id: Optional[str] = None, *,
              tracker: Any = None, spawn: Optional[Callable[..., Any]] = None,
              check_model: Callable[[], bool] = model_available,
              timeout: float = RUN_TIMEOUT_S) -> Dict[str, Any]:
    """Run one Researcher probe through Mastra and record the score. Returns result or {error}."""
    from arail.mcp.researcher_tool import validate_goal
    if validate_goal(goal):
        return {"error": "invalid_goal"}
    if experiment_id is not None and not (isinstance(experiment_id, str) and _EXP_ID.match(experiment_id)):
        return {"error": "experiment_missing"}
    nv = node_version()
    if not (is_installed() and nv and nv >= MIN_NODE):
        return {"error": "not_installed"}
    if not check_model():
        return {"error": "model_unavailable"}
    if tracker is None:
        from arail.skills.experiment_tracker import ExperimentTracker
        tracker = ExperimentTracker()
    if experiment_id is not None:
        try:
            tracker._load(experiment_id)
        except Exception:  # noqa: BLE001
            return {"error": "experiment_missing"}
    payload = json.dumps({"goal": goal, "exp_id": experiment_id})
    spawn = spawn or _spawn_child
    try:
        stdout = spawn(payload, mastra_env(), timeout)
    except TimeoutError:
        return {"error": "timeout"}
    except OSError:
        return {"error": "data_dir_not_writable"}
    try:
        parsed = parse_child_output(stdout)
    except ChildOutputError as exc:
        return {"error": str(exc)}
    created = parsed.get("experiment_id")
    if created is not None and not _EXP_ID.match(created):
        return {"error": "bad_child_output"}
    if experiment_id is not None and created is not None and experiment_id != created:
        # A caller-supplied id may only confirm the experiment the run created, never redirect the score.
        return {"error": "experiment_mismatch"}
    target = experiment_id or created
    if not target:
        return {"error": "experiment_missing"}
    return write_back(tracker, target, parsed)


def _spawn_child(payload: str, env: Dict[str, str], timeout: float) -> str:
    """Run run-once.mjs with shell=False, in its own process group; kill the group on timeout."""
    proc = subprocess.Popen(
        [shutil.which("node") or "node", "run-once.mjs"], cwd=str(MASTRA_DIR), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, start_new_session=True, shell=False)
    try:
        out, _ = proc.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise TimeoutError from None
    return out


def install() -> int:
    """``npm ci --ignore-scripts`` in the Mastra project, after the Node version check."""
    v = node_version()
    if v is None or v < MIN_NODE:
        print("Node 22.13 or newer is required for Mastra Studio.", file=sys.stderr)
        return 2
    rc = subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                        cwd=str(MASTRA_DIR), shell=False).returncode
    if rc != 0:
        return rc
    # Owner-accepted EE chunk is pinned: a lockfile bump that changes it must fail the install.
    return subprocess.run([shutil.which("node") or "node",
                           str(REPO_ROOT / "scripts" / "mastra" / "check-ee-loads.mjs"), "--installed"],
                          shell=False).returncode
