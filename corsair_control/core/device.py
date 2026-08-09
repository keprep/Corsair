"""Abstraction over liquidctl devices.

liquidctl exposes a different surface per driver: some devices name their fans
``fan1``/``fan2``, some only understand ``fan``, some drive the pump through a
named mode instead of a duty cycle. This module normalises all of that into a
single :class:`ManagedDevice` interface that the rest of the application can
program against, and degrades gracefully when a driver does not support a
feature.
"""

from __future__ import annotations

import inspect
import logging
import math
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

SPEED_KEY_RE = re.compile(r"^\s*(fan|pump)\s*(\d*)\s*speed\s*$", re.IGNORECASE)
DUTY_KEY_RE = re.compile(r"^\s*(fan|pump)\s*(\d*)\s*duty\s*$", re.IGNORECASE)
TEMP_KEY_RE = re.compile(r"temperature|temp\b", re.IGNORECASE)

#: Never drive a pump below this unless the user explicitly lowers the floor.
DEFAULT_PUMP_FLOOR = 50.0


class DeviceError(RuntimeError):
    """Raised when a device operation fails in a way the UI should surface."""


@dataclass
class Channel:
    """One controllable (or at least observable) fan/pump output."""

    channel_id: str
    label: str
    kind: str = "fan"  # "fan" | "pump"
    controllable: bool = True
    speed_key: str | None = None
    duty_key: str | None = None

    @property
    def is_pump(self) -> bool:
        return self.kind == "pump"

    @property
    def default_floor(self) -> float:
        return DEFAULT_PUMP_FLOOR if self.is_pump else 0.0


@dataclass
class LightingChannel:
    channel_id: str
    label: str
    modes: list[str] = field(default_factory=list)


@dataclass
class DeviceStatus:
    """A single snapshot of everything a device reported."""

    temperatures: dict[str, float] = field(default_factory=dict)
    speeds: dict[str, float] = field(default_factory=dict)
    duties: dict[str, float] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _normalise_channel(prefix: str, index: str) -> str:
    prefix = prefix.lower()
    return f"{prefix}{index}" if index else prefix


def _title(channel_id: str) -> str:
    match = re.match(r"^(fan|pump)(\d*)$", channel_id, re.IGNORECASE)
    if match:
        prefix, index = match.groups()
        return f"{prefix.capitalize()} {index}".strip()
    return channel_id.replace("_", " ").title()


class ManagedDevice:
    """Thread-safe wrapper around a single liquidctl device."""

    def __init__(self, backend: Any, *, demo: bool = False) -> None:
        self._dev = backend
        self._lock = threading.RLock()
        self._connected = False
        self._initialised = False
        self.demo = demo
        self.channels: list[Channel] = []
        self.lighting_channels: list[LightingChannel] = []
        self.pump_modes: list[str] = []
        self.last_status = DeviceStatus()
        self.last_error: str | None = None
        self._applied: dict[str, float] = {}

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------
    @property
    def description(self) -> str:
        return getattr(self._dev, "description", "Unknown device")

    @property
    def driver(self) -> str:
        return type(self._dev).__name__

    @property
    def key(self) -> str:
        """Stable identifier used as the profile key.

        Serial numbers would be nicer but reading them requires the device to
        be accessible, so the USB coordinates are the dependable fallback.
        """
        vid = getattr(self._dev, "vendor_id", 0) or 0
        pid = getattr(self._dev, "product_id", 0) or 0
        bus = getattr(self._dev, "bus", "") or ""
        address = getattr(self._dev, "address", "") or ""
        return f"{vid:04x}:{pid:04x}:{bus}:{address}".replace("/", "_")

    @property
    def bus_info(self) -> str:
        bus = getattr(self._dev, "bus", "?")
        address = getattr(self._dev, "address", "?")
        return f"{bus} @ {address}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ManagedDevice {self.description!r} {self.key}>"

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        with self._lock:
            if self._connected:
                return
            try:
                self._dev.connect()
            except Exception as exc:
                raise DeviceError(self._explain(exc)) from exc
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            if not self._connected:
                return
            try:
                self._dev.disconnect()
            except Exception as exc:  # pragma: no cover - best effort teardown
                log.debug("disconnect failed for %s: %s", self.key, exc)
            self._connected = False

    def initialize(self, **kwargs: Any) -> None:
        """Run the driver's handshake. Required by most Corsair devices."""
        with self._lock:
            self.connect()
            try:
                self._dev.initialize(**kwargs)
            except Exception as exc:
                raise DeviceError(self._explain(exc)) from exc
            self._initialised = True

    def probe(self) -> None:
        """Discover channels, pump modes and lighting after connecting."""
        with self._lock:
            self.pump_modes = self._discover_pump_modes()
            self.lighting_channels = self._discover_lighting()
            status = self.refresh()
            self.channels = self._discover_channels(status)
            # A driver can accept a ``pump_mode`` argument on a model that has
            # no pump at all (shared driver class); do not advertise it then.
            if not any(c.is_pump for c in self.channels):
                self.pump_modes = []

    def _explain(self, exc: Exception) -> str:
        text = str(exc) or exc.__class__.__name__
        lowered = text.lower()
        if "permission" in lowered or "access denied" in lowered or "errno 13" in lowered:
            return (
                f"{text} - no permission to talk to the USB device. Install the udev "
                "rules (see packaging/60-corsair-control.rules) and replug, or run the "
                "daemon as root."
            )
        return text

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    def _discover_channels(self, status: DeviceStatus) -> list[Channel]:
        found: dict[str, Channel] = {}

        # 1. Ask the driver. Most Corsair drivers keep an internal mapping of
        #    the channels they accept; the names differ per driver, so try the
        #    ones we know about.
        for attr in ("_speed_channels", "_fan_names", "_fan_channels", "_channels"):
            value = getattr(self._dev, attr, None)
            names: Iterable[str]
            if isinstance(value, dict):
                names = value.keys()
            elif isinstance(value, (list, tuple, set)):
                names = value
            else:
                continue
            for name in names:
                if not isinstance(name, str):
                    continue
                lowered = name.lower()
                if not (lowered.startswith("fan") or lowered.startswith("pump")):
                    continue
                kind = "pump" if lowered.startswith("pump") else "fan"
                found.setdefault(
                    lowered,
                    Channel(channel_id=lowered, label=_title(lowered), kind=kind),
                )

        # 2. Fall back to whatever the status report exposes. A device that
        #    reports "Fan 2 speed" has a fan 2, whatever the driver's internals
        #    look like.
        for key in list(status.speeds) + list(status.duties):
            lowered = key.lower()
            kind = "pump" if lowered.startswith("pump") else "fan"
            found.setdefault(lowered, Channel(channel_id=lowered, label=_title(lowered), kind=kind))

        # 3. Attach the status keys so the UI can show live RPM/duty.
        for channel in found.values():
            channel.speed_key = channel.channel_id if channel.channel_id in status.speeds else None
            channel.duty_key = channel.channel_id if channel.channel_id in status.duties else None

        # A pump that is driven by a named mode rather than a duty cycle must
        # not be presented as if a curve would work on it.
        if self.pump_modes:
            for channel in found.values():
                if channel.is_pump and not self._accepts_duty(channel.channel_id):
                    channel.controllable = False

        def sort_key(channel: Channel) -> tuple[int, str]:
            return (0 if channel.is_pump else 1, channel.channel_id)

        return sorted(found.values(), key=sort_key)

    def _accepts_duty(self, channel_id: str) -> bool:
        """Whether ``set_fixed_speed`` is plausible for this channel.

        Some drivers expose a pump that is only switchable between quiet /
        balanced / extreme. Those keep a ``_pump_modes``-ish table but reject a
        duty cycle outright, so we only trust an explicit speed-channel entry.
        """
        speed_channels = getattr(self._dev, "_speed_channels", None)
        if isinstance(speed_channels, dict):
            return channel_id in {k.lower() for k in speed_channels}
        return True

    def _discover_pump_modes(self) -> list[str]:
        """Detect drivers whose pump is set through ``initialize(pump_mode=…)``."""
        try:
            signature = inspect.signature(self._dev.initialize)
        except (TypeError, ValueError):  # pragma: no cover - exotic drivers
            return []
        if "pump_mode" not in signature.parameters:
            return []
        for attr in dir(type(self._dev)):
            if "pump_mode" not in attr.lower():
                continue
            value = getattr(type(self._dev), attr, None)
            if isinstance(value, dict) and value:
                return [str(k) for k in value]
            if isinstance(value, (list, tuple)) and value:
                return [str(v) for v in value]
        return ["quiet", "balanced", "extreme"]

    def _discover_lighting(self) -> list[LightingChannel]:
        modes: list[str] = []
        for attr in dir(type(self._dev)):
            if "color" not in attr.lower() or "mode" not in attr.lower():
                continue
            value = getattr(type(self._dev), attr, None)
            if isinstance(value, dict) and value:
                modes = [str(k) for k in value]
                break
            if isinstance(value, (list, tuple, set)) and value:
                modes = [str(v) for v in value]
                break
        if not modes:
            modes = ["fixed", "off"]

        names: list[str] = []
        for attr in ("_color_channels", "_led_channels", "_lighting_channels"):
            value = getattr(self._dev, attr, None)
            if isinstance(value, dict):
                names = [str(k) for k in value]
                break
            if isinstance(value, (list, tuple, set)):
                names = [str(v) for v in value]
                break
        if not names:
            if not hasattr(self._dev, "set_color"):
                return []
            names = ["led"]

        return [LightingChannel(channel_id=n, label=_title(n) or n, modes=modes) for n in names]

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def refresh(self) -> DeviceStatus:
        with self._lock:
            status = DeviceStatus(timestamp=time.monotonic())
            try:
                self.connect()
                raw = self._dev.get_status()
            except Exception as exc:
                status.error = self._explain(exc)
                self.last_error = status.error
                self.last_status = status
                return status

            self.last_error = None
            for entry in raw or ():
                try:
                    key, value, unit = entry[0], entry[1], entry[2] if len(entry) > 2 else ""
                except (TypeError, IndexError):  # pragma: no cover - driver oddity
                    continue
                self._ingest(status, str(key), value, str(unit))

            self.last_status = status
            return status

    def _ingest(self, status: DeviceStatus, key: str, value: Any, unit: str) -> None:
        speed = SPEED_KEY_RE.match(key)
        if speed and isinstance(value, (int, float)):
            status.speeds[_normalise_channel(*speed.groups())] = float(value)
            return

        duty = DUTY_KEY_RE.match(key)
        if duty and isinstance(value, (int, float)):
            status.duties[_normalise_channel(*duty.groups())] = float(value)
            return

        if unit.strip() in {"°C", "C", "degC"} or (TEMP_KEY_RE.search(key) and unit.strip() == ""):
            if isinstance(value, (int, float)):
                status.temperatures[key] = float(value)
                return

        if unit.strip() == "rpm" and isinstance(value, (int, float)):
            status.speeds[key.lower()] = float(value)
            return

        status.extras[key] = value

    def temperature_sensors(self) -> list[tuple[str, str]]:
        """(sensor_id, label) pairs this device contributes to the sensor hub."""
        return [
            (f"dev:{self.key}:{name}", f"{self.short_name} · {name}")
            for name in self.last_status.temperatures
        ]

    @property
    def short_name(self) -> str:
        text = self.description
        for prefix in ("Corsair ", "NZXT "):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        return text.split("(")[0].strip() or self.description

    # ------------------------------------------------------------------
    # control
    # ------------------------------------------------------------------
    def set_duty(self, channel_id: str, duty: float) -> None:
        duty = max(0.0, min(100.0, float(duty)))
        with self._lock:
            self.connect()
            try:
                self._dev.set_fixed_speed(channel=channel_id, duty=int(round(duty)))
            except TypeError:
                # A few drivers use positional arguments only.
                try:
                    self._dev.set_fixed_speed(channel_id, int(round(duty)))
                except Exception as exc:
                    raise DeviceError(self._explain(exc)) from exc
            except Exception as exc:
                raise DeviceError(self._explain(exc)) from exc
            self._applied[channel_id] = duty

    def applied_duty(self, channel_id: str) -> float | None:
        return self._applied.get(channel_id)

    def set_speed_profile(self, channel_id: str, points: Sequence[tuple[float, float]]) -> None:
        """Hand a curve to the device so it keeps working without software.

        Only some devices (Commander Pro, older Hydro units) support this. It
        is offered as "offload to hardware" in the UI, because a hardware curve
        survives a crash of the control software.
        """
        with self._lock:
            self.connect()
            profile = [(int(t), int(d)) for t, d in points]
            try:
                self._dev.set_speed_profile(channel=channel_id, profile=profile)
            except TypeError:
                try:
                    self._dev.set_speed_profile(channel_id, profile)
                except Exception as exc:
                    raise DeviceError(self._explain(exc)) from exc
            except Exception as exc:
                raise DeviceError(self._explain(exc)) from exc

    @property
    def supports_hardware_curves(self) -> bool:
        return hasattr(self._dev, "set_speed_profile")

    def set_pump_mode(self, mode: str) -> None:
        with self._lock:
            self.connect()
            try:
                self._dev.initialize(pump_mode=mode)
            except Exception as exc:
                raise DeviceError(self._explain(exc)) from exc

    def set_lighting(self, channel_id: str, mode: str, colors: Sequence[Sequence[int]]) -> None:
        with self._lock:
            self.connect()
            payload = [list(c) for c in colors]
            try:
                self._dev.set_color(channel=channel_id, mode=mode, colors=payload)
            except TypeError:
                try:
                    self._dev.set_color(channel_id, mode, payload)
                except Exception as exc:
                    raise DeviceError(self._explain(exc)) from exc
            except Exception as exc:
                raise DeviceError(self._explain(exc)) from exc

    @property
    def supports_lighting(self) -> bool:
        return bool(self.lighting_channels) and hasattr(self._dev, "set_color")


# ----------------------------------------------------------------------
# demo backend
# ----------------------------------------------------------------------
class _DemoBackend:
    """A believable stand-in for an AIO plus a fan hub.

    Fans have inertia and the liquid temperature reacts to the airflow, which
    makes the curve editor behave like the real thing when demoing or testing.
    """

    description = "Corsair Hydro H150i Elite Capellix (demo)"
    vendor_id = 0x1B1C
    product_id = 0x0C21
    bus = "demo"
    address = "demo0"
    serial_number = "DEMO-0001"

    _speed_channels = {"fan1": None, "fan2": None, "fan3": None, "pump": None}
    _COLOR_MODES = {"fixed": 0, "breathing": 1, "rainbow": 2, "off": 3}
    _color_channels = {"led": 0}

    def __init__(self) -> None:
        self._duties = {"fan1": 40.0, "fan2": 40.0, "fan3": 40.0, "pump": 70.0}
        self._rpm = {"fan1": 700.0, "fan2": 700.0, "fan3": 700.0, "pump": 2000.0}
        self._liquid = 32.0
        self._t = 0.0

    def connect(self, **_: Any) -> None:
        return None

    def disconnect(self, **_: Any) -> None:
        return None

    def initialize(self, pump_mode: str | None = None, **_: Any) -> list[tuple[str, Any, str]]:
        if pump_mode:
            self._duties["pump"] = {"quiet": 55.0, "balanced": 75.0, "extreme": 100.0}.get(
                pump_mode, 75.0
            )
        return [("Firmware version", "2.10.219", "")]

    def _tick(self) -> None:
        self._t += 1
        for name, duty in self._duties.items():
            top = 2900.0 if name == "pump" else 2100.0
            target = (duty / 100.0) * top
            self._rpm[name] += (target - self._rpm[name]) * 0.35
            self._rpm[name] = max(0.0, self._rpm[name] + random.uniform(-12, 12))
        airflow = sum(self._duties[f] for f in ("fan1", "fan2", "fan3")) / 3.0
        load = 34 + 16 * math.sin(self._t / 17.0) ** 2
        equilibrium = load + (100 - airflow) * 0.16
        self._liquid += (equilibrium - self._liquid) * 0.12

    def get_status(self) -> list[tuple[str, Any, str]]:
        self._tick()
        return [
            ("Liquid temperature", round(self._liquid, 1), "°C"),
            ("Fan 1 speed", int(self._rpm["fan1"]), "rpm"),
            ("Fan 1 duty", round(self._duties["fan1"], 1), "%"),
            ("Fan 2 speed", int(self._rpm["fan2"]), "rpm"),
            ("Fan 2 duty", round(self._duties["fan2"], 1), "%"),
            ("Fan 3 speed", int(self._rpm["fan3"]), "rpm"),
            ("Fan 3 duty", round(self._duties["fan3"], 1), "%"),
            ("Pump speed", int(self._rpm["pump"]), "rpm"),
            ("Pump duty", round(self._duties["pump"], 1), "%"),
            ("Firmware version", "2.10.219", ""),
        ]

    def set_fixed_speed(self, channel: str, duty: int, **_: Any) -> None:
        if channel == "fan":
            for name in ("fan1", "fan2", "fan3"):
                self._duties[name] = float(duty)
            return
        if channel not in self._duties:
            raise ValueError(f"unknown channel {channel}")
        self._duties[channel] = float(duty)

    def set_speed_profile(self, channel: str, profile: Sequence[tuple[int, int]], **_: Any) -> None:
        if channel not in self._duties and channel != "fan":
            raise ValueError(f"unknown channel {channel}")

    def set_color(self, channel: str, mode: str, colors: Sequence[Sequence[int]], **_: Any) -> None:
        if mode not in self._COLOR_MODES:
            raise ValueError(f"unsupported mode {mode}")


class _DemoCommanderBackend(_DemoBackend):
    description = "Corsair Commander Pro (demo)"
    product_id = 0x0C10
    address = "demo1"
    serial_number = "DEMO-0002"
    _speed_channels = {f"fan{i}": None for i in range(1, 7)}

    def __init__(self) -> None:
        super().__init__()
        self._duties = {f"fan{i}": 35.0 for i in range(1, 7)}
        self._rpm = {f"fan{i}": 600.0 for i in range(1, 7)}

    def get_status(self) -> list[tuple[str, Any, str]]:
        self._tick()
        rows: list[tuple[str, Any, str]] = [
            ("Temperature 1", round(self._liquid - 4, 1), "°C"),
        ]
        for i in range(1, 7):
            rows.append((f"Fan {i} speed", int(self._rpm[f"fan{i}"]), "rpm"))
            rows.append((f"Fan {i} duty", round(self._duties[f"fan{i}"], 1), "%"))
        return rows

    def set_fixed_speed(self, channel: str, duty: int, **_: Any) -> None:
        if channel == "fan":
            for name in self._duties:
                self._duties[name] = float(duty)
            return
        if channel not in self._duties:
            raise ValueError(f"unknown channel {channel}")
        self._duties[channel] = float(duty)


def demo_devices() -> list[ManagedDevice]:
    return [
        ManagedDevice(_DemoBackend(), demo=True),
        ManagedDevice(_DemoCommanderBackend(), demo=True),
    ]
