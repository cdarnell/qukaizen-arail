"""Arail — runtime hardware discovery.

No hardcoded target machine. ARAIL is a blueprint people fork onto whatever
they own; a 16 GB laptop is the design floor, not an M-series workstation.
This module probes what's actually there (RAM, accelerator) and persists it
so the rest of the lab (chat defaults, the AeroLLM secondary-model cap,
setup warnings) can make decisions grounded in the real machine instead of
an assumption baked into a comment.

Do not hardcode a specific chip or memory figure here or anywhere it's
consumed — see CLAUDE.md "Model checkpoint paths stay relative / env-driven"
for the same discipline applied to weights; this is the analogous rule for
the machine itself.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# Deliberately not `from arail import config` — config.py pulls in
# python-dotenv and arail.model_defaults at import time, and hardware
# discovery should work standalone (e.g. from a bare setup.sh probe script)
# without that whole chain. Same ARAIL_DATA_DIR/LAB_ROOT convention, just
# resolved directly.

# Design floor: the smallest machine ARAIL is expected to run acceptably on.
# Used only to decide "stay conservative" vs "there's headroom" — never as
# a target to build features around.
MIN_SUPPORTED_RAM_GB = 16.0


@dataclass
class HardwareProfile:
    total_ram_gb: float
    accelerator: str  # "mlx" | "cuda" | "cpu" | "unknown"
    platform_system: str
    platform_machine: str
    below_min_supported: bool
    source: str  # how total_ram_gb was determined


def _detect_ram_gb() -> tuple[Optional[float], str]:
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            return int(out.stdout.strip()) / (1024**3), "sysctl hw.memsize"
        if system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb / (1024**2), "/proc/meminfo"
        if system == "Windows":
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return stat.ullTotalPhys / (1024**3), "GlobalMemoryStatusEx"
    except Exception:  # noqa: BLE001 — any probe failure just means "unknown"
        pass
    return None, "undetected"


def _detect_accelerator() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "mlx"
    try:
        subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=5, check=True,
        )
        return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def discover_hardware() -> HardwareProfile:
    """Probe the running machine. Never guesses a target — reports what's here."""
    ram_gb, source = _detect_ram_gb()
    accelerator = _detect_accelerator()
    resolved_ram = ram_gb if ram_gb is not None else MIN_SUPPORTED_RAM_GB
    return HardwareProfile(
        total_ram_gb=round(resolved_ram, 1),
        accelerator=accelerator,
        platform_system=platform.system(),
        platform_machine=platform.machine(),
        below_min_supported=resolved_ram < MIN_SUPPORTED_RAM_GB,
        source=source,
    )


def _profile_path() -> Path:
    lab_root = os.getenv("LAB_ROOT", "lab")
    data_dir = os.getenv("ARAIL_DATA_DIR", str(Path(lab_root) / "data"))
    return Path(data_dir).expanduser() / "hardware.json"


def persist(profile: HardwareProfile) -> Path:
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(profile), indent=2) + "\n")
    return path


def load_or_discover(*, force_refresh: bool = False) -> HardwareProfile:
    """Read the persisted profile, or discover + persist one if absent/stale."""
    path = _profile_path()
    if not force_refresh and path.exists():
        try:
            return HardwareProfile(**json.loads(path.read_text()))
        except Exception:  # noqa: BLE001 — corrupt cache, re-discover
            pass
    profile = discover_hardware()
    try:
        persist(profile)
    except Exception:  # noqa: BLE001 — read-only fs etc.; discovery still valid
        pass
    return profile


def secondary_model_cap_b(profile: Optional[HardwareProfile] = None) -> float:
    """Max total-params (billions) it's stable to keep resident as the
    AeroLLM/AirLLM *secondary* model on the discovered machine.

    Conservative, coarse bands driven by observed resident footprint at
    4-bit quantization (~0.55 GB/B). Not a promise, a stability guardrail:
    operators can always set a smaller AEROLLM_MODEL themselves, but this
    is what ARAIL will accept without a loud warning.
    """
    profile = profile or load_or_discover()
    ram = profile.total_ram_gb
    if ram < 16:
        return 3.0
    if ram < 24:
        return 8.0
    if ram < 32:
        return 14.0
    if ram < 48:
        return 32.0
    if ram < 64:
        return 70.0
    return 235.0
