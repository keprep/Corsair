"""Profiles: what each channel should do, and how that is persisted."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from corsair_control.core.config import config_dir, ensure_dirs, profiles_path
from corsair_control.core.curve import FanCurve, preset_curve

log = logging.getLogger(__name__)

MODE_CURVE = "curve"
MODE_FIXED = "fixed"
MODE_MANUAL = "manual"  # leave the channel alone entirely
MODES = (MODE_CURVE, MODE_FIXED, MODE_MANUAL)


@dataclass
class ChannelConfig:
    mode: str = MODE_CURVE
    fixed_duty: float = 50.0
    sensor_id: str | None = None
    curve: FanCurve = field(default_factory=FanCurve)
    min_duty: float = 0.0
    max_duty: float = 100.0
    allow_zero_rpm: bool = False
    offload_to_hardware: bool = False

    def clamp(self, duty: float) -> float:
        """Apply the per-channel limits, including the zero-RPM guard."""
        duty = max(self.min_duty, min(self.max_duty, duty))
        if not self.allow_zero_rpm and duty < 1.0:
            duty = max(1.0, self.min_duty)
        return round(duty, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "fixed_duty": self.fixed_duty,
            "sensor_id": self.sensor_id,
            "curve": self.curve.to_list(),
            "min_duty": self.min_duty,
            "max_duty": self.max_duty,
            "allow_zero_rpm": self.allow_zero_rpm,
            "offload_to_hardware": self.offload_to_hardware,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChannelConfig":
        mode = str(data.get("mode", MODE_CURVE))
        return cls(
            mode=mode if mode in MODES else MODE_CURVE,
            fixed_duty=float(data.get("fixed_duty", 50.0)),
            sensor_id=data.get("sensor_id"),
            curve=FanCurve.from_list(data.get("curve")),
            min_duty=float(data.get("min_duty", 0.0)),
            max_duty=float(data.get("max_duty", 100.0)),
            allow_zero_rpm=bool(data.get("allow_zero_rpm", False)),
            offload_to_hardware=bool(data.get("offload_to_hardware", False)),
        )


@dataclass
class LightingConfig:
    mode: str = "fixed"
    colors: list[list[int]] = field(default_factory=lambda: [[240, 165, 0]])

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "colors": self.colors}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LightingConfig":
        colors = data.get("colors") or [[240, 165, 0]]
        return cls(mode=str(data.get("mode", "fixed")), colors=[list(c) for c in colors])


@dataclass
class DeviceConfig:
    channels: dict[str, ChannelConfig] = field(default_factory=dict)
    lighting: dict[str, LightingConfig] = field(default_factory=dict)
    pump_mode: str | None = None

    def channel(self, channel_id: str) -> ChannelConfig:
        return self.channels.setdefault(channel_id, ChannelConfig())

    def to_dict(self) -> dict[str, Any]:
        return {
            "channels": {k: v.to_dict() for k, v in self.channels.items()},
            "lighting": {k: v.to_dict() for k, v in self.lighting.items()},
            "pump_mode": self.pump_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceConfig":
        return cls(
            channels={
                k: ChannelConfig.from_dict(v) for k, v in (data.get("channels") or {}).items()
            },
            lighting={
                k: LightingConfig.from_dict(v) for k, v in (data.get("lighting") or {}).items()
            },
            pump_mode=data.get("pump_mode"),
        )


@dataclass
class Profile:
    name: str
    devices: dict[str, DeviceConfig] = field(default_factory=dict)

    def device(self, key: str) -> DeviceConfig:
        return self.devices.setdefault(key, DeviceConfig())

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "devices": {k: v.to_dict() for k, v in self.devices.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        return cls(
            name=str(data.get("name", "Unnamed")),
            devices={
                k: DeviceConfig.from_dict(v) for k, v in (data.get("devices") or {}).items()
            },
        )

    def copy_as(self, name: str) -> "Profile":
        return Profile.from_dict({**self.to_dict(), "name": name})


class ProfileStore:
    """Holds every profile plus which one is active, backed by a JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or profiles_path()
        self.profiles: dict[str, Profile] = {}
        self.active_name: str = "Default"
        #: True when the profiles were read from the system-wide file the
        #: daemon writes. Saving still goes to :attr:`path`.
        self.loaded_from_system = False

    # ------------------------------------------------------------------
    def load(self) -> None:
        path = self.path
        if not path.exists():
            # A user session can still read a profile set the daemon wrote.
            system = Path("/etc") / "corsair-control" / "profiles.json"
            if system.exists() and system != path:
                path = system
                self.loaded_from_system = True
            else:
                self._bootstrap()
                return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Cannot read profiles from %s: %s", path, exc)
            self._bootstrap()
            return

        self.profiles = {
            str(name): Profile.from_dict(data)
            for name, data in (raw.get("profiles") or {}).items()
        }
        self.active_name = str(raw.get("active", "Default"))
        if not self.profiles:
            self._bootstrap()
        elif self.active_name not in self.profiles:
            self.active_name = next(iter(self.profiles))

    def _bootstrap(self) -> None:
        for name in ("Silent", "Balanced", "Performance"):
            self.profiles.setdefault(name, Profile(name=name))
        self.active_name = "Balanced"

    def save(self) -> None:
        ensure_dirs()
        payload = {
            "version": 1,
            "active": self.active_name,
            "profiles": {name: profile.to_dict() for name, profile in self.profiles.items()},
        }
        target = self.path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(target)
        except OSError as exc:
            log.error("Cannot write profiles to %s: %s", target, exc)
            raise

    # ------------------------------------------------------------------
    @property
    def active(self) -> Profile:
        if self.active_name not in self.profiles:
            self.profiles[self.active_name] = Profile(name=self.active_name)
        return self.profiles[self.active_name]

    def names(self) -> list[str]:
        return sorted(self.profiles)

    def __iter__(self) -> Iterator[Profile]:
        return iter(self.profiles.values())

    def activate(self, name: str) -> Profile:
        if name not in self.profiles:
            self.profiles[name] = Profile(name=name)
        self.active_name = name
        return self.profiles[name]

    def add(self, name: str, *, copy_from: str | None = None) -> Profile:
        base = self.profiles.get(copy_from or "")
        profile = base.copy_as(name) if base else Profile(name=name)
        self.profiles[name] = profile
        return profile

    def rename(self, old: str, new: str) -> bool:
        if old not in self.profiles or new in self.profiles or not new.strip():
            return False
        profile = self.profiles.pop(old)
        profile.name = new
        self.profiles[new] = profile
        if self.active_name == old:
            self.active_name = new
        return True

    def remove(self, name: str) -> bool:
        if name not in self.profiles or len(self.profiles) <= 1:
            return False
        del self.profiles[name]
        if self.active_name == name:
            self.active_name = next(iter(self.profiles))
        return True

    # ------------------------------------------------------------------
    def seed_defaults(self, device_key: str, channels: list, default_sensor: str | None) -> bool:
        """Give a freshly detected device a sensible configuration.

        Returns ``True`` when something was added, so the caller knows whether
        it needs to persist the store.
        """
        changed = False
        for profile_name, preset in (
            ("Silent", "Silent"),
            ("Balanced", "Balanced"),
            ("Performance", "Performance"),
        ):
            profile = self.profiles.get(profile_name)
            if profile is None:
                continue
            device = profile.device(device_key)
            for channel in channels:
                if channel.channel_id in device.channels:
                    continue
                device.channels[channel.channel_id] = ChannelConfig(
                    mode=MODE_CURVE if channel.controllable else MODE_MANUAL,
                    sensor_id=default_sensor,
                    curve=preset_curve(preset, pump=channel.is_pump),
                    min_duty=channel.default_floor,
                )
                changed = True

        # Any other user-made profile gets the same treatment so that switching
        # to it does not leave a fan uncontrolled.
        for profile in self.profiles.values():
            if profile.name in {"Silent", "Balanced", "Performance"}:
                continue
            device = profile.device(device_key)
            for channel in channels:
                if channel.channel_id in device.channels:
                    continue
                device.channels[channel.channel_id] = ChannelConfig(
                    mode=MODE_CURVE if channel.controllable else MODE_MANUAL,
                    sensor_id=default_sensor,
                    curve=preset_curve("Balanced", pump=channel.is_pump),
                    min_duty=channel.default_floor,
                )
                changed = True
        return changed


def default_store() -> ProfileStore:
    store = ProfileStore(config_dir() / "profiles.json")
    store.load()
    return store
