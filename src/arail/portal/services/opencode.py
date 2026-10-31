"""opencode service module — subprocess lifecycle for opencode serve.

Design decisions (from ARCHITECTURE.md @ 50ce5ad):
- Direct iframe, no reverse proxy.
- OPENCODE_SERVER_PASSWORD NOT set.
- Loopback-only binding (--hostname 127.0.0.1 hard-coded, no env override).
- Readiness probe: GET /doc (OpenAPI JSON). No /healthz endpoint.
- --port REQUIRED (opencode default is 0 / OS-assigned).
- Log capped at 10 MB; rotated to opencode.log.1 on start.
- Module-level Lock serialises start/stop/restart against concurrent callers.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Minimum secret length to redact (skip trivially short values to avoid
# false positives and the empty-string infinite-loop hazard).
_MIN_SECRET_LEN: int = 8

# Sentinel written in place of a redacted token.
_REDACTED: bytes = b"***REDACTED***"


class _RedactingLogWriter:
    """Write-time redactor for opencode subprocess stdout/stderr.

    Replaces each provider token with ``***REDACTED***`` before the bytes
    reach disk. Carries a tail buffer of ``max(len(s) for s in secrets) - 1``
    bytes across successive ``write()`` calls so a token that straddles a
    chunk boundary is still caught.

    Thread-safety: single daemon reader thread calls write(); the main
    thread only calls close(). No internal lock needed.
    """

    def __init__(self, path: "Path", secrets: "list[bytes]") -> None:
        # Filter: non-empty, minimum length.
        self._secrets: list[bytes] = [
            s for s in secrets if s and len(s) >= _MIN_SECRET_LEN
        ]
        self._path = path
        self._fh = path.open("ab")
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            _log.warning("opencode log: chmod 0600 failed: %s", exc)
        # Tail buffer length = max secret length - 1 (catches cross-chunk splits).
        max_len = max((len(s) for s in self._secrets), default=0)
        self._tail_len: int = max(0, max_len - 1)
        self._tail: bytes = b""
        self._closed: bool = False

    def write(self, chunk: bytes) -> int:
        if self._closed:
            return 0
        data = self._tail + chunk
        try:
            redacted = self._redact(data)
            if self._tail_len:
                # Hold back the last (tail_len) bytes so a secret split across
                # the next chunk boundary can be caught on the next write.
                if len(redacted) > self._tail_len:
                    flush_up_to = len(redacted) - self._tail_len
                    self._tail = redacted[flush_up_to:]
                    redacted = redacted[:flush_up_to]
                else:
                    # Buffer is too small to flush any bytes yet.
                    self._tail = redacted
                    redacted = b""
            else:
                self._tail = b""
            if redacted:
                self._fh.write(redacted)
                self._fh.flush()
        except Exception as exc:  # noqa: BLE001
            _log.warning("opencode log: write-time redaction error (chunk written raw): %s", exc)
            try:
                self._tail = b""
                self._fh.write(chunk)
                self._fh.flush()
            except Exception:
                pass
        return len(chunk)

    def _redact(self, data: bytes) -> bytes:
        for secret in self._secrets:
            data = data.replace(secret, _REDACTED)
        return data

    def flush_tail(self) -> None:
        """Flush any buffered tail bytes — call at EOF (reader thread exit)."""
        if self._tail and not self._closed:
            try:
                redacted = self._redact(self._tail)
                self._fh.write(redacted)
                self._fh.flush()
            except Exception:
                pass
            self._tail = b""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.flush_tail()
        finally:
            try:
                self._fh.close()
            except Exception:
                pass


def _pipe_reader_thread(read_fd: int, writer: "_RedactingLogWriter") -> None:
    """Daemon thread: drain a pipe read end into a RedactingLogWriter."""
    try:
        with os.fdopen(read_fd, "rb", buffering=0) as pipe:
            while True:
                chunk = pipe.read(8192)
                if not chunk:
                    break
                writer.write(chunk)
    except Exception as exc:
        _log.debug("opencode log reader thread exiting: %s", exc)
    finally:
        writer.flush_tail()


def _open_log_with_redactor(
    log_file: "Path",
    env: "dict[str, str]",
) -> "tuple[int, _RedactingLogWriter, threading.Thread]":
    """Open a pipe + redacting writer for subprocess stdout/stderr.

    Returns (write_fd, writer, reader_thread).
    Caller must pass write_fd to Popen and start reader_thread before
    Popen's first output arrives (safe to start after Popen — pipe buffers).
    Caller must close write_fd after Popen exits so the reader thread
    sees EOF.
    """
    # Tombstone: if the log already has content from a prior run, drop it.
    # We cannot safely retroactively redact unknown prior tokens.
    if log_file.exists() and log_file.stat().st_size > 0:
        try:
            log_file.unlink()
        except OSError:
            pass

    # Also drop any rotated .log.1 from prior runs (may contain old tokens).
    rotated = log_file.with_suffix(".log.1")
    if rotated.exists():
        try:
            rotated.unlink()
        except OSError:
            pass

    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.touch()

    # Collect secrets from env (only keys we export as tokens).
    _TOKEN_KEYS = {
        "ANTHROPIC_API_KEY",
        "NVIDIA_API_KEY",
        "OPENROUTER_API_KEY",
        "HF_TOKEN",
        "MODEL_API_KEY",
        "OPENCODE_API_KEY",
    }
    secrets: list[bytes] = []
    for key in _TOKEN_KEYS:
        val = env.get(key, "")
        if val:
            secrets.append(val.encode())

    writer = _RedactingLogWriter(log_file, secrets)

    read_fd, write_fd = os.pipe()
    thread = threading.Thread(
        target=_pipe_reader_thread,
        args=(read_fd, writer),
        daemon=True,
        name="opencode-log-redactor",
    )
    return write_fd, writer, thread

# ── LLM-ready check cache ──────────────────────────────────────────────────
_LLM_READY_TTL_S: float = 5.0
_LLM_READY_CACHE: dict[str, Any] = {"key": None, "result": None, "ts": 0.0}

# ── Provider token env-var map (mirrors _PROVIDER_KEY_ENVS in app.py) ─────
# Maps active provider id → env-var name for the cloud token so opencode.json
# can reference via env=["NAME"] without embedding the secret.
_PROVIDER_TOKEN_ENV: dict[str, str] = {
    "claude":      "ANTHROPIC_API_KEY",
    "nvidia":      "NVIDIA_API_KEY",
    "openrouter":  "OPENROUTER_API_KEY",
    "huggingface": "HF_TOKEN",
    "custom":      "MODEL_API_KEY",
}

# Mapping from ARAIL provider id → opencode's built-in provider id
_PROVIDER_OC_ID: dict[str, str] = {
    "claude":      "anthropic",
    "nvidia":      "nvidia",
    "openrouter":  "openrouter",
    "huggingface": "huggingface",
    "custom":      "custom",
}

# ── Public constants ────────────────────────────────────────────────────────

PORT_DEFAULT: int = 4096
HOST: str = "127.0.0.1"
LOG_PATH = Path("lab/logs/opencode.log")
READINESS_PATH: str = "/doc"
_LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB

# ── Module-level lock (F-PROC-4, F-CONFIG-3) ───────────────────────────────
_lock = threading.Lock()

# ── Provider constants (mirrors app.py) ────────────────────────────────────
_CLOUD_PROVIDER_BASES: dict[str, str] = {
    "claude":      "https://api.anthropic.com/v1",
    "nvidia":      "https://integrate.api.nvidia.com/v1",
    "openrouter":  "https://openrouter.ai/api/v1",
    "huggingface": "https://api-inference.huggingface.co",
    "custom":      "",  # reads MODEL_API_BASE
}

_PROVIDER_KEY_ENVS: dict[str, str] = {
    "claude":      "ANTHROPIC_API_KEY",
    "nvidia":      "NVIDIA_API_KEY",
    "openrouter":  "OPENROUTER_API_KEY",
    "huggingface": "HF_TOKEN",
    "custom":      "MODEL_API_KEY",
}


# ── Public API ──────────────────────────────────────────────────────────────

def is_installed() -> bool:
    """True iff opencode binary is on PATH. Never raises."""
    try:
        return bool(shutil.which("opencode"))
    except Exception:
        return False


def is_running(port: int = PORT_DEFAULT) -> bool:
    """TCP probe — True if something is listening on HOST:port.

    Returns within ~300 ms (sub-second timeout matches _port_open in app.py).
    TCP-open does NOT mean opencode is ready; use _wait_ready for that.
    """
    try:
        with socket.create_connection((HOST, port), timeout=0.3):
            return True
    except OSError:
        return False


def _is_opencode_on_port(port: int) -> bool:
    """True iff GET HOST:port/doc returns OpenAPI JSON identifying opencode.

    Used by start() to distinguish "we already own this port" (idempotent
    success) from "something else hijacked the port" (real conflict). The
    /doc path is opencode's OpenAPI schema; presence of the "openapi" key
    plus an info.title matching opencode is the fingerprint.

    Returns False on any error (timeout, non-200, non-JSON, missing keys).
    """
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            f"http://{HOST}:{port}{READINESS_PATH}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=0.5) as resp:  # noqa: S310 — loopback only
            if resp.status != 200:
                return False
            body = resp.read(4096)  # /doc is small; head is enough
        data = json.loads(body)
        if not isinstance(data, dict):
            return False
        if "openapi" not in data:
            return False
        title = (data.get("info") or {}).get("title", "").lower()
        return "opencode" in title
    except Exception:
        return False


def start(port: int = PORT_DEFAULT) -> dict[str, Any]:
    """[UPDATED Sprint 2] Spawn opencode with lab-curated config.

    Steps (under module _lock):
      1. is_installed() / is_running() pre-checks (Sprint 1).
      2. _maybe_rotate_log() (Sprint 1).
      3. regenerate_config()   — NEW: write lab/.opencode/opencode.json first.
         If write fails: return {"ok": False, "error": "config_write: ..."}.
      4. Popen with OPENCODE_CONFIG_DIR, OPENCODE_DISABLE_AUTOUPDATE,
         OPENCODE_LOG_LEVEL=WARN, plus _compute_source_env() — NEW Sprint 2 env.
      5. Return {"ok": True, "pid": int} (unchanged shape).

    Does NOT set OPENCODE_SERVER_PASSWORD (architecture decision).
    Hard-codes --hostname 127.0.0.1 (F-SEC-6, no env override).
    """
    with _lock:
        if not is_installed():
            return {"ok": False, "error": "opencode not installed"}

        # Idempotent start: if the port is already taken AND it's opencode
        # answering on /doc, treat the call as success rather than confusing
        # the user with a contradictory "port busy" badge while the iframe
        # would have loaded fine. Bug surfaced when the post-start reload
        # raced opencode's bind window: the page rendered "not running",
        # the user clicked Start again, and got "port busy" while opencode
        # was in fact already serving on 4096. F-RESTART-* / live-test fix.
        if is_running(port):
            if _is_opencode_on_port(port):
                _log.info("opencode start: idempotent success — already serving on %s", port)
                return {"ok": True, "already_running": True, "port": port}
            return {"ok": False, "error": "port busy"}

        # Prepare log file (F-PROC-5, F-PROC-6)
        log_file = Path(LOG_PATH)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        _maybe_rotate_log(log_file)

        # NEW: write opencode.json before spawning (Sprint 2)
        # regenerate_config() uses _lock internally via try — we call the inner
        # helper here instead to avoid deadlock since we already hold _lock.
        cfg_result = _regenerate_config_unlocked()
        if not cfg_result.get("ok"):
            return {"ok": False, "error": f"config_write: {cfg_result.get('error', 'unknown')}"}

        # Build env — fresh every call (F-RESTART-2)
        env = {
            **os.environ,
            "OPENCODE_CONFIG_DIR": str(_config_dir()),   # NEW Sprint 2
            "OPENCODE_DISABLE_AUTOUPDATE": "true",        # Sprint 1 follow-up
            "OPENCODE_LOG_LEVEL": "WARN",                 # Sprint 1 follow-up (F-SEC-CRED-2 partial)
            **_compute_source_env(),
        }

        try:
            write_fd, _writer, reader_thread = _open_log_with_redactor(log_file, env)
            reader_thread.start()
            proc = subprocess.Popen(
                ["opencode", "serve", "--port", str(port), "--hostname", HOST],
                env=env,
                stdout=write_fd,
                stderr=write_fd,
                start_new_session=True,
            )
            os.close(write_fd)  # child inherits it; we don't need the write end
            _log.info("opencode started pid=%s port=%s config=%s",
                      proc.pid, port, cfg_result.get("path", "?"))
            return {"ok": True, "pid": proc.pid}
        except Exception as exc:
            _log.error("opencode start failed: %s", exc)
            return {"ok": False, "error": str(exc)}


def stop(port: int = PORT_DEFAULT) -> dict[str, Any]:
    """Kill all processes bound to HOST:port via lsof.

    Returns {"ok": True, "killed": [pid, ...]} or
            {"ok": False, "error": str}.
    Sends SIGTERM first; if still running after 2 s, sends SIGKILL.
    Never raises.
    """
    with _lock:
        return _stop_unlocked(port)


def _stop_unlocked(port: int) -> dict[str, Any]:
    """Inner stop — caller MUST hold _lock."""
    if not shutil.which("lsof"):
        return {"ok": False, "error": "lsof unavailable"}
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True,
        )
        raw_pids = [p.strip() for p in result.stdout.strip().split() if p.strip()]
        if not raw_pids:
            return {"ok": True, "killed": []}

        # SIGTERM first
        for pid in raw_pids:
            try:
                subprocess.run(["kill", pid], check=False)
            except Exception:
                pass

        # Wait up to 2 s, then SIGKILL stragglers
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            still = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True,
            ).stdout.strip().split()
            if not still:
                break
            time.sleep(0.1)

        still = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True,
        ).stdout.strip().split()
        for pid in still:
            try:
                subprocess.run(["kill", "-9", pid], check=False)
            except Exception:
                pass

        _log.info("opencode stopped port=%s killed=%s", port, raw_pids)
        return {"ok": True, "killed": raw_pids}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def restart(port: int = PORT_DEFAULT) -> dict[str, Any]:
    """Best-effort restart: stop → wait port free → start → wait ready.

    Caller MUST fire this in a daemon thread (it blocks up to ~13 s).
    Returns {"ok": True} only after readiness confirmed.
    Returns {"ok": False, "error": "<phase>: <detail>"} on timeout.
    """
    with _lock:
        # 1. Stop
        stop_result = _stop_unlocked(port)
        if not stop_result.get("ok"):
            _log.warning("opencode restart/stop failed: %s", stop_result)
            # Continue anyway — maybe it was already down

        # 2. Wait up to 3 s for port to be free (F-RESTART-3)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not is_running(port):
                break
            time.sleep(0.1)
        if is_running(port):
            return {"ok": False, "error": "stop: port still bound after 3 s"}

        # 3. Start (calls _start_inner without locking again since we hold it)
        start_result = _start_inner(port)
        if not start_result.get("ok"):
            return {"ok": False, "error": f"start: {start_result.get('error', 'unknown')}"}

        # 4. Wait ready
        if not _wait_ready(port, timeout_s=10.0):
            return {"ok": False, "error": "ready: opencode did not respond on /doc within 10 s"}

        return {"ok": True}


def _start_inner(port: int) -> dict[str, Any]:
    """Start without acquiring _lock — caller must hold it. (Sprint 2 updated)"""
    if not is_installed():
        return {"ok": False, "error": "opencode not installed"}

    if is_running(port):
        return {"ok": False, "error": "port busy"}

    log_file = Path(LOG_PATH)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    _maybe_rotate_log(log_file)

    # Write config first (Sprint 2)
    cfg_result = _regenerate_config_unlocked()
    if not cfg_result.get("ok"):
        return {"ok": False, "error": f"config_write: {cfg_result.get('error', 'unknown')}"}

    env = {
        **os.environ,
        "OPENCODE_CONFIG_DIR": str(_config_dir()),
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
        "OPENCODE_LOG_LEVEL": "WARN",
        **_compute_source_env(),
    }

    try:
        write_fd, _writer, reader_thread = _open_log_with_redactor(log_file, env)
        reader_thread.start()
        proc = subprocess.Popen(
            ["opencode", "serve", "--port", str(port), "--hostname", HOST],
            env=env,
            stdout=write_fd,
            stderr=write_fd,
            start_new_session=True,
        )
        os.close(write_fd)  # child inherits it; we don't need the write end
        _log.info("opencode started pid=%s port=%s", proc.pid, port)
        return {"ok": True, "pid": proc.pid}
    except Exception as exc:
        _log.error("opencode start failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _wait_ready(port: int, timeout_s: float) -> bool:
    """Poll GET http://HOST:port/doc every 200 ms until 200 OK or timeout.

    Uses `requests` with a short per-call timeout (1.0 s).
    Returns True on first 200, False on timeout.
    """
    import requests  # already in pyproject.toml (A10)

    url = f"http://{HOST}:{port}{READINESS_PATH}"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=1.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _maybe_rotate_log(log_file: Path) -> None:
    """Rotate log to .log.1 if it exceeds _LOG_MAX_BYTES (F-PROC-6).

    Also hardens the rotated file to 0o600 (same threat model as the active log).
    """
    if log_file.exists() and log_file.stat().st_size > _LOG_MAX_BYTES:
        rotated = log_file.with_suffix(".log.1")
        try:
            log_file.rename(rotated)
            try:
                os.chmod(rotated, 0o600)
            except OSError:
                pass
        except OSError:
            pass


def _shim_api_key() -> str:
    try:
        from arail.portal import auth as _auth
        return _auth.read_shim_token() or "not-needed"
    except Exception:  # noqa: BLE001 - OpenCode still starts; the shim will just 401
        return "not-needed"


def _compute_source_env() -> dict[str, str]:
    """[UPDATED Sprint 2] Translate active Compute Source into opencode env vars.

    Reads (no writes): imports from app.py helpers via lazy import to
    avoid circular dependency at module load time.
    Never logs token values (F-SEC-2).

    Mapping (CHANGED from Sprint 1):
      my_machine  → OPENCODE_API_BASE = lab shim http://127.0.0.1:<PORT>/api/openai/v1
                    OPENCODE_MODEL    = _CHAT_MODEL_LOAD_STATE['model'] or ''
                    OPENCODE_API_KEY  = 'not-needed'
      cloud       → OPENCODE_API_BASE = cloud provider base URL
                    OPENCODE_MODEL    = MODEL_NAME
                    OPENCODE_API_KEY  = token (belt-and-suspenders)
                    <provider-env>    = token (NEW — what opencode.json references)
    Unknown provider falls back to my_machine mapping.
    """
    # Lazy import — avoids circular import at module level and matches
    # the pattern used in the providers_active hook.
    try:
        from arail.portal.app import (  # type: ignore[import]
            _load_active_provider,
            _provider_token,
            _get_chat_model_load_state,
        )
        provider = _load_active_provider()
        token = _provider_token(provider) if provider != "my_machine" else ""
        load_state = _get_chat_model_load_state()
        active_model = load_state.get("model") or os.getenv("MODEL_NAME", "")
    except Exception:
        provider = "my_machine"
        token = ""
        active_model = os.getenv("MODEL_NAME", "")

    portal_port = int(os.getenv("PORTAL_PORT", "8080"))

    if provider == "my_machine" or provider not in _CLOUD_PROVIDER_BASES:
        # Point opencode at the lab-side OpenAI shim (NOT Ollama default)
        shim_base = f"http://127.0.0.1:{portal_port}/api/openai/v1"
        return {
            "OPENCODE_API_BASE": shim_base,
            "OPENCODE_MODEL": active_model,
            # Shim-scoped token (arl1s_): valid only under /api/openai/v1/. NEVER the portal bearer:
            # this env is readable by the shell the model drives.
            "OPENCODE_API_KEY": _shim_api_key(),
        }

    # Cloud provider — set BOTH the canonical env name AND OPENCODE_API_KEY
    if provider == "custom":
        base = os.getenv("MODEL_API_BASE") or ""
    else:
        base = _CLOUD_PROVIDER_BASES.get(provider, "")

    env_var_name = _PROVIDER_TOKEN_ENV.get(provider)  # e.g. "ANTHROPIC_API_KEY"
    result: dict[str, str] = {
        "OPENCODE_API_BASE": base,
        "OPENCODE_MODEL": active_model,
        "OPENCODE_API_KEY": token,  # legacy compat
    }
    if env_var_name:
        result[env_var_name] = token  # what opencode.json's env=["NAME"] reads

    return result


# ── Config path helpers ────────────────────────────────────────────────────

def _config_dir() -> Path:
    """Return Path to lab/.opencode/ (passed as OPENCODE_CONFIG_DIR to subprocess).

    Rooted at config.LAB_ROOT (default 'lab/'). Pure: reads env at call time.
    """
    try:
        from arail.config import LAB_ROOT  # type: ignore[import]
        return Path(LAB_ROOT) / ".opencode"
    except Exception:
        return Path("lab") / ".opencode"


def _config_path() -> Path:
    """Return Path to lab/.opencode/opencode.json."""
    return _config_dir() / "opencode.json"


# ── Lab system prompt ──────────────────────────────────────────────────────

def lab_system_prompt(tier: str) -> str:
    """Return the multi-line system prompt for the build/plan agents.

    Pure. Tier-aware: maximus version mentions Workbench surfaces.
    Accepts legacy "max"/"min" tier names via the v1.0.0 compat shim
    (removed in v1.1.0).
    """
    # v1.0.0 legacy-tier shim — apply at function boundary so tests and
    # callers passing literal "max"/"min" continue to work.
    tier = {"min": "minimalist", "max": "maximus"}.get(tier, tier)
    workbench_section = ""
    if tier == "maximus":
        workbench_section = """
      - The Workbench tab (maximus-tier) gives you Jupyter Lab, Marimo,
        Open Notebook, and opencode — each a separate iframe card.
        You're currently running inside opencode."""

    return f"""You are coding inside ARAIL — an autoresearch AI lab blueprint.
The repository root is the working directory.

Read these files when relevant:
- CLAUDE.md (the orientation file for AI agents in this repo)
- AGENTS.md (the platform-porting manifest for new platforms)
- docs/agents.md (the agent loader contract)

Conventions:
- Sprints live in sprints/<YYYY-MM-DD>-<slug>/. The /sprint skill
  orchestrates visionary → architect → builder → architect-review →
  qa → ship via committed artifacts (VISION/ARCHITECTURE/BUILD_LOG/
  REVIEW/TEST_REPORT).
- Agents live in lab/pkb/agents/<id>/AGENT.md + <id>.py.
- Secrets live in lab/data/secrets.env (chmod 0600, git-ignored).
  NEVER write credentials to other paths and NEVER echo them back.
- LAB_MODE defaults to 'airgapped' — do not reach external services
  unless the user explicitly enables hybrid mode.
- The internal Python package name is `arail`. Imports must not
  break when the lab is rebranded (LAB_NAME / LAB_TAGLINE).{workbench_section}

To switch which model handles your prompts, change the Compute Source
in the lab's Chat tab — opencode picks up the new model on its next
restart (automatic on switch).

Use the slash commands /lab-status, /sprint-current, /skills-list,
/agents-status, /kb-search, /claude-md to orient quickly.

Match the existing code style. Branch names use the qukaizen/<slug>
prefix. Commit messages should be concise; prefer fixing root causes
over masking symptoms."""


# ── Config renderer ────────────────────────────────────────────────────────

def _render_opencode_config(
    *,
    provider: str,
    model: str | None,
    portal_port: int,
    tier: str,
    models_list: list[dict] | None = None,
    lab_mode: str = "airgapped",
) -> dict:
    """Pure function: build the dict that becomes opencode.json.

    Parameters:
      provider    — 'my_machine' | 'claude' | 'nvidia' | 'openrouter' |
                    'huggingface' | 'custom'
      model       — active model id (None when no model loaded)
      portal_port — int, used in the 'lab-local' provider baseURL
      tier        — 'minimalist' | 'maximus' (governs the agent prompt)
      models_list — optional pre-fetched scan results; included in the
                    lab-local models map when provider='my_machine'
      lab_mode    — 'airgapped' | 'hybrid'

    Critical security invariant (F-SEC-CRED-1):
      - This function NEVER reads _provider_token() or any secret env var.
      - Cloud tokens go through _compute_source_env() (subprocess env only).
      - The JSON output must never contain token strings.

    Returns: dict (NOT json string — caller serializes).
    Pure: no filesystem, no env reads beyond explicit parameters.
    """
    # v1.0.0 legacy-tier shim — apply at function boundary so tests and
    # callers passing literal "max"/"min" continue to work.
    tier = {"min": "minimalist", "max": "maximus"}.get(tier, tier)

    # Airgapped override: force my_machine regardless (F-AIRGAP-2)
    if lab_mode != "hybrid" and provider not in ("my_machine",):
        provider = "my_machine"

    prompt = lab_system_prompt(tier)

    # Build provider block + enabled list
    if provider == "my_machine" or provider not in _PROVIDER_OC_ID:
        # Local inference shim
        base_url = f"http://127.0.0.1:{portal_port}/api/openai/v1"
        provider_block: dict = {
            "lab-local": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "ARAIL Lab",
                "options": {
                    "baseURL": base_url,
                    "apiKey": _shim_api_key(),
                },
                "models": _build_models_map(model, models_list),
            }
        }
        enabled_providers = ["lab-local"]
        model_ref = f"lab-local/{model}" if model else "lab-local/unknown"
        agent_model_ref = model_ref
    else:
        # Cloud provider
        oc_id = _PROVIDER_OC_ID[provider]
        model_id = model or "unknown"
        provider_block = {
            oc_id: {
                "name": oc_id.capitalize(),
                "options": {},
                "models": {
                    model_id: {
                        "name": model_id,
                        "tools": True,
                    }
                },
            }
        }
        enabled_providers = [oc_id]
        model_ref = f"{oc_id}/{model_id}"
        agent_model_ref = model_ref

    return {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
        "instructions": [
            "AGENTS.md",
            "CLAUDE.md",
            "docs/agents.md",
        ],
        "provider": provider_block,
        "enabled_providers": enabled_providers,
        "model": model_ref,
        "small_model": model_ref,
        "agent": {
            "build": {
                "model": agent_model_ref,
                "prompt": prompt,
                "tools": {
                    "write": True,
                    "edit": True,
                    "bash": True,
                    "read": True,
                    "grep": True,
                    "glob": True,
                    "list": True,
                },
            },
            "plan": {
                "model": agent_model_ref,
                "prompt": prompt,
                "tools": {
                    "write": False,
                    "edit": False,
                    "bash": False,
                    "read": True,
                    "grep": True,
                    "glob": True,
                    "list": True,
                },
            },
        },
        "command": {
            "lab-status": {
                "description": "Summarize lab status — active goal, sprint, agents, KB stats.",
                "agent": "build",
                "template": (
                    "Read $REPO_ROOT/lab/data/goals/active.json (if present), "
                    "the latest sprints/<id>/SPRINT.md by mtime, and "
                    "`ls $REPO_ROOT/lab/pkb/agents/`. Summarize: current goal, "
                    "in-progress sprint phase, count of agents discovered, "
                    "count of files in $REPO_ROOT/lab/pkb/sources/. Be terse."
                ),
            },
            "sprint-current": {
                "description": "Show the in-progress sprint's SPRINT.md and next undone phase.",
                "agent": "build",
                "template": (
                    "Find the most recently modified sprints/*/SPRINT.md. "
                    "Read it. Identify the next phase whose Status is not 'done'. "
                    "Print the phase name + the artifact path that would carry "
                    "its output (VISION.md/ARCHITECTURE.md/BUILD_LOG.md/"
                    "REVIEW.md/TEST_REPORT.md)."
                ),
            },
            "skills-list": {
                "description": "List skills installed under lab/pkb/skills/.",
                "agent": "build",
                "template": (
                    "Run `ls -1 $REPO_ROOT/lab/pkb/skills/`. For each entry, "
                    "read the first heading from its SKILL.md if present. "
                    "Output: skill-id  —  one-line summary."
                ),
            },
            "agents-status": {
                "description": "List agents and their last-activity timestamps.",
                "agent": "build",
                "template": (
                    "Run `ls -1 $REPO_ROOT/lab/pkb/agents/`. For each entry, "
                    "read AGENT.md's first paragraph. If a state file exists at "
                    "$REPO_ROOT/lab/data/agents/<id>/state.json, include the "
                    "'last_seen' field. Output one line per agent."
                ),
            },
            "kb-search": {
                "description": "Text search over the lab knowledge base files (no ranking).",
                "agent": "build",
                "template": (
                    "Search the KB files for: $ARGUMENTS. "
                    "Run `rg -n -i -- \"$ARGUMENTS\" $REPO_ROOT/lab/pkb` "
                    "and summarize the top 5 hits with file paths."
                ),
            },
            "claude-md": {
                "description": "Read CLAUDE.md and recap the conventions.",
                "agent": "build",
                "template": (
                    "Read $REPO_ROOT/CLAUDE.md. Output a 5-bullet summary "
                    "covering: what ARAIL is, how to invoke /sprint, how "
                    "agents are loaded, where secrets live, and the LAB_MODE "
                    "airgap default."
                ),
            },
        },
        "permission": {
            "edit": "allow",
            "bash": {"*": "ask"},
        },
    }


def _build_models_map(
    active_model: str | None,
    models_list: list[dict] | None,
) -> dict:
    """Build the models dict for the lab-local provider block."""
    models: dict = {}
    if models_list:
        for m in models_list:
            mid = m.get("id") or m.get("name") or ""
            if mid:
                models[mid] = {"name": mid, "tools": True, "reasoning": False}
    if active_model and active_model not in models:
        models[active_model] = {"name": active_model, "tools": True, "reasoning": False}
    return models


# ── LLM-ready check ────────────────────────────────────────────────────────

def llm_ready_check(force: bool = False) -> dict:
    """Decide whether opencode can start meaningfully right now.

    Returns:
      {
        "ok": bool,
        "reason": str | None,    # 'no_llm' | 'loading' | 'no_token' | None
        "hint": str | None,      # human-readable next step
        "chat_url": "/chat" | None,
        "provider": str,
        "model": str | None,
      }

    Never raises. Cached for up to _LLM_READY_TTL_S seconds.
    force=True bypasses cache.
    """
    global _LLM_READY_CACHE
    try:
        from arail.portal.app import (  # type: ignore[import]
            _load_active_provider,
            _provider_token,
            _get_chat_model_load_state,
        )
        provider = _load_active_provider()
    except Exception:
        provider = "my_machine"

    # Cache key = (provider, state, model)
    try:
        load_state = _get_chat_model_load_state()  # type: ignore[possibly-undefined]
        state_val = load_state.get("state", "ready")
        model_val = load_state.get("model")
    except Exception:
        state_val = "ready"
        model_val = None

    cache_key = (provider, state_val, model_val)
    now = time.monotonic()
    if (
        not force
        and _LLM_READY_CACHE["key"] == cache_key
        and (now - _LLM_READY_CACHE["ts"]) < _LLM_READY_TTL_S
    ):
        return dict(_LLM_READY_CACHE["result"])

    result = _compute_llm_ready(provider, state_val, model_val)
    _LLM_READY_CACHE["key"] = cache_key
    _LLM_READY_CACHE["result"] = result
    _LLM_READY_CACHE["ts"] = now
    return dict(result)


def _compute_llm_ready(provider: str, state_val: str, model_val: str | None) -> dict:
    """Inner logic for llm_ready_check — separated for testability."""
    if provider == "my_machine" or provider not in _PROVIDER_TOKEN_ENV:
        if state_val == "ready" and model_val:
            return {
                "ok": True,
                "reason": None,
                "hint": None,
                "chat_url": None,
                "provider": provider,
                "model": model_val,
            }
        if state_val == "loading":
            return {
                "ok": False,
                "reason": "loading",
                "hint": "Model is loading — try again in a moment.",
                "chat_url": "/chat",
                "provider": provider,
                "model": model_val,
            }
        # state == 'error' or state == 'ready' but no model
        return {
            "ok": False,
            "reason": "no_llm",
            "hint": "Load a model in Chat first.",
            "chat_url": "/chat",
            "provider": provider,
            "model": None,
        }
    else:
        # Cloud provider
        try:
            from arail.portal.app import _provider_token  # type: ignore[import]
            token = _provider_token(provider)
        except Exception:
            token = ""
        if not token:
            return {
                "ok": False,
                "reason": "no_token",
                "hint": f"Save a {provider} API key in Chat → Manage providers.",
                "chat_url": "/chat",
                "provider": provider,
                "model": model_val,
            }
        return {
            "ok": True,
            "reason": None,
            "hint": None,
            "chat_url": None,
            "provider": provider,
            "model": model_val,
        }


def invalidate_llm_ready_cache() -> None:
    """Invalidate the LLM-ready cache. Called after state changes."""
    global _LLM_READY_CACHE
    _LLM_READY_CACHE["key"] = None
    _LLM_READY_CACHE["ts"] = 0.0


# ── Config regeneration ────────────────────────────────────────────────────

def _regenerate_config_unlocked() -> dict:
    """Inner regenerate — caller MUST hold _lock."""
    import json as _json

    try:
        from arail.portal.app import (  # type: ignore[import]
            _load_active_provider,
            _get_chat_model_load_state,
        )
        provider = _load_active_provider()
        load_state = _get_chat_model_load_state()
        model = load_state.get("model") if load_state.get("state") == "ready" else None
    except Exception:
        provider = "my_machine"
        model = None

    # Read raw tier; apply v1.0.0 legacy min/max → minimalist/maximus shim.
    _raw_tier = os.getenv("LAB_TIER", "minimalist").strip().lower()
    tier = {"min": "minimalist", "max": "maximus"}.get(_raw_tier, _raw_tier)
    lab_mode = os.getenv("LAB_MODE", os.getenv("ARAIL_MODE", "airgapped")).strip().lower()

    try:
        cfg_dict = _render_opencode_config(
            provider=provider,
            model=model,
            portal_port=int(os.getenv("PORTAL_PORT", "8080")),
            tier=tier,
            lab_mode=lab_mode,
        )
    except Exception as exc:
        return {"ok": False, "error": f"render: {exc}"}

    cfg_dir = _config_dir()
    cfg_file = _config_path()

    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        try:
            cfg_dir.chmod(0o700)
        except OSError:
            pass

        tmp = cfg_file.with_suffix(".json.tmp")
        content = _json.dumps(cfg_dict, indent=2, sort_keys=True)
        tmp.write_text(content, encoding="utf-8")
        with tmp.open("rb") as fh:
            import os as _os
            _os.fsync(fh.fileno())
        tmp.replace(cfg_file)
        try:
            cfg_file.chmod(0o600)   # holds the shim-scoped token (arl1s_)
        except OSError:
            pass

        return {
            "ok": True,
            "path": str(cfg_file),
            "model": model,
            "provider": provider,
        }
    except Exception as exc:
        try:
            tmp_path = cfg_file.with_suffix(".json.tmp")
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        _log.error("opencode: _regenerate_config_unlocked write failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def regenerate_config(*, force: bool = False) -> dict:
    """Write lab/.opencode/opencode.json from current lab state.

    Acquires _lock, then delegates to _regenerate_config_unlocked().
    Returns {"ok": bool, "path": str, "model": str|None, "provider": str}.
    On write failure: {"ok": False, "error": str}.
    Does NOT delete existing config on failure (F-CONFIG-3).
    """
    with _lock:
        return _regenerate_config_unlocked()


def install_hint() -> dict[str, str]:
    """Return platform-specific install instructions for opencode.

    Pure: reads platform.system() + os.uname() only. No I/O.
    """
    system = platform.system()
    if system == "Darwin":
        return {
            "platform": "darwin",
            "command": "brew install sst/tap/opencode",
            "docs_url": "https://opencode.ai/docs",
        }
    if system == "Linux":
        # Distinguish WSL
        try:
            uname_r = os.uname().release.lower()
        except Exception:
            uname_r = ""
        if "microsoft" in uname_r or "wsl" in uname_r:
            return {
                "platform": "wsl",
                "command": "curl -fsSL https://opencode.ai/install | bash",
                "docs_url": "https://opencode.ai/docs",
            }
        return {
            "platform": "linux",
            "command": "curl -fsSL https://opencode.ai/install | bash",
            "docs_url": "https://opencode.ai/docs",
        }
    # Windows or other
    if system == "Windows":
        return {
            "platform": "windows",
            "command": "winget install sst.opencode",
            "docs_url": "https://opencode.ai/docs",
        }
    return {
        "platform": "other",
        "command": "curl -fsSL https://opencode.ai/install | bash",
        "docs_url": "https://opencode.ai/docs",
    }
