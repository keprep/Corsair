"""Alarms for the failures that are otherwise silent.

A pump that quietly drops to a few hundred RPM is the most expensive fault in
a water loop, and nothing on the desktop tells you about it. Every rule here
needs the condition to hold for a while before it fires, because a single bad
tacho reading is not a fault.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corsair_control.core.engine import Snapshot

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

KIND_PUMP_SLOW = "pump_slow"
KIND_FAN_STALLED = "fan_stalled"
KIND_DEVICE_ERROR = "device_error"
KIND_TEMPERATURE = "temperature"


@dataclass(frozen=True)
class Alarm:
    key: str
    kind: str
    severity: str
    message: str
    since: float

    @property
    def age(self) -> float:
        return time.monotonic() - self.since


@dataclass
class AlarmSettings:
    enabled: bool = True
    pump_minimum_rpm: float = 400.0
    fan_stall_duty: float = 25.0
    temperature_warning: float = 85.0
    #: How long a condition has to hold before it counts as an alarm.
    debounce_seconds: float = 12.0

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "pump_minimum_rpm": self.pump_minimum_rpm,
            "fan_stall_duty": self.fan_stall_duty,
            "temperature_warning": self.temperature_warning,
            "debounce_seconds": self.debounce_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "AlarmSettings":
        data = data or {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class AlarmMonitor:
    settings: AlarmSettings = field(default_factory=AlarmSettings)
    _pending: dict[str, tuple[float, str, str, str]] = field(default_factory=dict)
    _active: dict[str, Alarm] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def active(self) -> list[Alarm]:
        return sorted(
            self._active.values(),
            key=lambda alarm: (alarm.severity != SEVERITY_CRITICAL, alarm.since),
        )

    def clear(self) -> None:
        self._pending.clear()
        self._active.clear()

    # ------------------------------------------------------------------
    def evaluate(self, snapshot: "Snapshot") -> tuple[list[Alarm], list[Alarm]]:
        """Update the state. Returns (newly raised, newly cleared)."""
        if not self.settings.enabled:
            cleared = list(self._active.values())
            self.clear()
            return [], cleared

        now = time.monotonic()
        seen = dict(self._collect(snapshot))

        for key, (kind, severity, message) in seen.items():
            if key in self._active:
                continue
            first_seen = self._pending.get(key, (now, kind, severity, message))[0]
            self._pending[key] = (first_seen, kind, severity, message)

        raised: list[Alarm] = []
        for key, (first_seen, kind, severity, message) in list(self._pending.items()):
            if key not in seen:
                del self._pending[key]
                continue
            if now - first_seen >= self.settings.debounce_seconds:
                alarm = Alarm(
                    key=key, kind=kind, severity=severity, message=message, since=first_seen
                )
                self._active[key] = alarm
                raised.append(alarm)
                del self._pending[key]

        cleared: list[Alarm] = []
        for key in list(self._active):
            if key not in seen:
                cleared.append(self._active.pop(key))

        return raised, cleared

    # ------------------------------------------------------------------
    def _collect(self, snapshot: "Snapshot") -> Iterable[tuple[str, tuple[str, str, str]]]:
        settings = self.settings

        for device in snapshot.devices:
            if device.error:
                yield (
                    f"{device.key}:error",
                    (KIND_DEVICE_ERROR, SEVERITY_CRITICAL, f"{device.name}: {device.error}"),
                )
                # A device we cannot read cannot produce meaningful channel
                # alarms, so skip the rest for it.
                continue

            for channel in device.channels:
                key = f"{device.key}/{channel.channel_id}"
                if channel.rpm is None:
                    continue
                if channel.kind == "pump":
                    if channel.rpm < settings.pump_minimum_rpm:
                        yield (
                            f"{key}:pump",
                            (
                                KIND_PUMP_SLOW,
                                SEVERITY_CRITICAL,
                                f"{device.name} · {channel.label}: only "
                                f"{channel.rpm:.0f} rpm - check the pump",
                            ),
                        )
                    continue
                duty = channel.duty if channel.duty is not None else channel.target_duty
                if duty is not None and duty >= settings.fan_stall_duty and channel.rpm <= 0:
                    yield (
                        f"{key}:stall",
                        (
                            KIND_FAN_STALLED,
                            SEVERITY_WARNING,
                            f"{device.name} · {channel.label}: {duty:.0f} % but no rotation",
                        ),
                    )

        hottest = max(snapshot.sensors.values(), default=None) if snapshot.sensors else None
        if hottest is not None and hottest >= settings.temperature_warning:
            yield (
                "temperature",
                (
                    KIND_TEMPERATURE,
                    SEVERITY_WARNING,
                    f"{hottest:.0f} °C reached the warning threshold",
                ),
            )
