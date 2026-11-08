"""PluginManager — clone GitHub repos and integrate them as lab tools.

Usage from portal:
    POST /api/plugins/install  {"github_url": "https://github.com/user/repo"}

The manager:
1. Clones the repo into ./plugins/<name>/
2. Reads README + requirements.txt
3. Installs deps into the active venv
4. Registers the plugin in a manifest
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from arail.activity import activity_log
from arail.config import DATA_DIR


PLUGINS_DIR = DATA_DIR / "plugins"
MANIFEST_FILE = PLUGINS_DIR / "manifest.json"

# Allowed GitHub URL patterns
_GITHUB_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.\-]+)/(?P<repo>[A-Za-z0-9_.\-]+?)(?:\.git)?/?$"
)


class PluginManager:
    """Manages installation and lifecycle of GitHub-sourced plugins."""

    def __init__(self) -> None:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Install ──────────────────────────────────────────────────────

    def install(self, github_url: str) -> Dict[str, Any]:
        """Clone a GitHub repo and register it as a plugin."""
        match = _GITHUB_RE.match(github_url.strip())
        if not match:
            raise ValueError(f"Invalid GitHub URL: {github_url}")

        owner = match.group("owner")
        repo = match.group("repo")
        name = f"{owner}/{repo}"
        dest = PLUGINS_DIR / owner / repo

        if dest.exists():
            raise ValueError(f"Plugin '{name}' is already installed.")

        activity_log.emit("plugins", f"Cloning {name}...", "info")

        dest.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", github_url, str(dest)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr.strip()}")

        activity_log.emit("plugins", f"Cloned {name}. Reading metadata...", "info")

        # Read metadata
        meta = self._read_metadata(dest, name)

        # Install requirements if present
        req_file = dest / "requirements.txt"
        if req_file.exists():
            activity_log.emit("plugins", f"Installing dependencies for {name}...", "info")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
                capture_output=True, text=True, timeout=300,
            )

        # Register
        meta["status"] = "active"
        meta["installed_at"] = _now()
        manifest = self._load_manifest()
        manifest[name] = meta
        self._save_manifest(manifest)

        activity_log.emit(
            "plugins",
            f"Plugin '{name}' installed successfully. "
            "Restart the lab so the egress guard covers any HTTP library it added.",
            "success",
        )
        return meta

    def uninstall(self, name: str) -> None:
        """Remove a plugin by name (owner/repo)."""
        manifest = self._load_manifest()
        if name not in manifest:
            raise ValueError(f"Plugin '{name}' not found.")

        parts = name.split("/", 1)
        if len(parts) == 2:
            dest = PLUGINS_DIR / parts[0] / parts[1]
        else:
            dest = PLUGINS_DIR / name

        if dest.exists():
            shutil.rmtree(dest)

        del manifest[name]
        self._save_manifest(manifest)
        activity_log.emit("plugins", f"Plugin '{name}' uninstalled.", "info")

    # ── Query ────────────────────────────────────────────────────────

    def list_plugins(self) -> List[Dict[str, Any]]:
        manifest = self._load_manifest()
        return list(manifest.values())

    def get_plugin(self, name: str) -> Optional[Dict[str, Any]]:
        return self._load_manifest().get(name)

    def get_readme(self, name: str) -> Optional[str]:
        parts = name.split("/", 1)
        if len(parts) == 2:
            dest = PLUGINS_DIR / parts[0] / parts[1]
        else:
            dest = PLUGINS_DIR / name
        for candidate in ["README.md", "readme.md", "README.rst", "README"]:
            p = dest / candidate
            if p.exists():
                return p.read_text()
        return None

    def toggle(self, name: str, active: bool) -> Dict[str, Any]:
        manifest = self._load_manifest()
        if name not in manifest:
            raise ValueError(f"Plugin '{name}' not found.")
        manifest[name]["status"] = "active" if active else "inactive"
        self._save_manifest(manifest)
        return manifest[name]

    # ── Internal ─────────────────────────────────────────────────────

    def _read_metadata(self, path: Path, name: str) -> Dict[str, Any]:
        """Read plugin metadata from arail-plugin.json or fallback to heuristics."""
        meta: Dict[str, Any] = {
            "name": name,
            "type": "framework",
            "description": "",
            "version": "unknown",
        }

        # Try arail-plugin.json first
        plugin_json = path / "arail-plugin.json"
        if plugin_json.exists():
            try:
                declared = json.loads(plugin_json.read_text())
                meta.update(declared)
                return meta
            except (json.JSONDecodeError, OSError):
                pass

        # Fallback: parse README first line as description
        readme = self.get_readme(name) if (path / "README.md").exists() else None
        if readme:
            first_line = ""
            for line in readme.split("\n"):
                stripped = line.strip().lstrip("#").strip()
                if stripped and not stripped.startswith("!"):
                    first_line = stripped
                    break
            meta["description"] = first_line[:200]

        # Detect type from files
        if (path / "setup.py").exists() or (path / "pyproject.toml").exists():
            meta["type"] = "framework"
        if any(path.glob("*.gguf")) or any(path.glob("*.safetensors")):
            meta["type"] = "model"

        return meta

    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        if not MANIFEST_FILE.exists():
            return {}
        try:
            return json.loads(MANIFEST_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_manifest(self, manifest: Dict[str, Dict[str, Any]]) -> None:
        MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, default=str))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
