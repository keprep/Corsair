"""Filesystem locations and application-wide settings."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from corsair_control.version import APP_SLUG

log = logging.getLogger(__name__)

SYSTEM_CONFIG_DIR = Path("/etc") / APP_SLUG


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / fallback


def config_dir() -> Path:
    """Directory holding settings and profiles.

    The daemon usually runs as root, in which case ``/etc/corsair-control`` is
    used so that the GUI (running as the desktop user) and the daemon can be
    pointed at the same profile set.
    """
    override = os.environ.get("CORSAIR_CONTROL_CONFIG_DIR")
    if override:
        return Path(override)
    if os.geteuid() == 0:
        return SYSTEM_CONFIG_DIR
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_SLUG


def state_dir() -> Path:
    override = os.environ.get("CORSAIR_CONTROL_STATE_DIR")
    if override:
        return Path(override)
    if os.geteuid() == 0:
        return Path("/var/lib") / APP_SLUG
    return _xdg("XDG_STATE_HOME", ".local/state") / APP_SLUG


def profiles_path() -> Path:
    return config_dir() / "profiles.json"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def ensure_dirs() -> None:
    for path in (config_dir(), state_dir()):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - depends on host permissions
            log.warning("Cannot create %s: %s", path, exc)


@dataclass
class Settings:
    """User preferences that are not part of a fan profile."""

    active_profile: str = "Default"
    poll_interval: float = 1.5
    apply_on_start: bool = True
    start_minimised: bool = False
    close_to_tray: bool = True
    history_seconds: int = 300
    accent: str = "#f0a500"
    temperature_unit: str = "C"
    # Safety net: if any monitored temperature exceeds this value every
    # controlled channel is driven to 100 % regardless of its curve.
    emergency_temperature: float = 90.0
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        path = settings_path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Cannot read settings from %s: %s", path, exc)
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        ensure_dirs()
        path = settings_path()
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # pragma: no cover - depends on host permissions
            log.warning("Cannot write settings to %s: %s", path, exc)
