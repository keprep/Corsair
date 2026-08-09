"""Mainboard fan control through the kernel's hwmon PWM interface.

The backend deliberately mimics the small slice of the liquidctl driver API
that :class:`~corsair_control.core.device.ManagedDevice` uses, so mainboard
fans flow through exactly the same channels, curves and profiles as the
Corsair hardware does.

Writing to ``/sys/class/hwmon/*/pwmN`` needs root on virtually every distro.
When the process cannot write, the channels are still discovered and shown -
just marked read-only with an explanation, which is far more useful than
hiding them.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger(__name__)

HWMON_ROOT = Path("/sys/class/hwmon")

PWM_RE = re.compile(r"^pwm(\d+)$")

#: Kernel drivers that expose hardware this application already talks to over
#: USB. Controlling the same fan through two paths fights itself, so these are
#: skipped unless the user asks for them explicitly.
SKIP_CHIPS = {
    "corsair-cpro",
    "corsaircpro",
    "corsair_cpro",
    "corsairpsu",
    "corsair-psu",
    "nzxt-kraken2",
    "nzxt-kraken3",
    "nzxt-smart2",
}

#: pwmN_enable values.
ENABLE_FULL = 0  # no control, full speed
ENABLE_MANUAL = 1
ENABLE_AUTOMATIC = 2


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    raw = _read(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _write_int(path: Path, value: int) -> None:
    with path.open("w") as handle:
        handle.write(str(int(value)))


class HwmonBackend:
    """One hwmon chip with at least one PWM output."""

    vendor_id = 0
    product_id = 0
    bus = "hwmon"

    def __init__(self, directory: Path, chip: str) -> None:
        self.directory = directory
        self.chip = chip
        self.address = directory.name
        self.description = f"{chip} (mainboard)"
        self._pwm: dict[str, Path] = {}
        self._enable: dict[str, Path] = {}
        self._fan: dict[str, Path] = {}
        self._original_enable: dict[str, int] = {}
        self._scan()
        self._speed_channels = dict.fromkeys(self._pwm)

    # ------------------------------------------------------------------
    def _scan(self) -> None:
        for pwm_path in sorted(self.directory.glob("pwm*")):
            match = PWM_RE.match(pwm_path.name)
            if not match:
                continue
            index = match.group(1)
            channel = f"fan{index}"
            self._pwm[channel] = pwm_path
            enable = self.directory / f"pwm{index}_enable"
            if enable.exists():
                self._enable[channel] = enable
            fan_input = self.directory / f"fan{index}_input"
            if fan_input.exists():
                self._fan[channel] = fan_input

    @property
    def stable_key(self) -> str:
        """hwmonN numbering shuffles between boots; the parent device does not."""
        ident = ""
        link = self.directory / "device"
        try:
            ident = os.path.basename(os.path.realpath(link))
        except OSError:  # pragma: no cover - sysfs oddity
            ident = ""
        return f"hwmon:{self.chip}:{ident or self.address}"

    @property
    def channels(self) -> list[str]:
        return list(self._pwm)

    def writable(self, channel: str) -> bool:
        path = self._pwm.get(channel)
        return bool(path and os.access(path, os.W_OK))

    @property
    def any_writable(self) -> bool:
        return any(self.writable(channel) for channel in self._pwm)

    # ------------------------------------------------------------------
    # liquidctl-shaped API
    # ------------------------------------------------------------------
    def connect(self, **_: Any) -> None:
        return None

    def disconnect(self, **_: Any) -> None:
        return None

    def initialize(self, **_: Any) -> list[tuple[str, Any, str]]:
        for channel, path in self._enable.items():
            value = _read_int(path)
            if value is not None:
                self._original_enable.setdefault(channel, value)
        return [("Chip", self.chip, ""), ("Path", str(self.directory), "")]

    def get_status(self) -> list[tuple[str, Any, str]]:
        rows: list[tuple[str, Any, str]] = []
        for channel, path in self._pwm.items():
            index = channel.removeprefix("fan")
            raw = _read_int(path)
            if raw is not None:
                rows.append((f"Fan {index} duty", round(raw / 255 * 100, 1), "%"))
            fan_input = self._fan.get(channel)
            if fan_input is not None:
                rpm = _read_int(fan_input)
                if rpm is not None:
                    rows.append((f"Fan {index} speed", rpm, "rpm"))
        return rows

    def set_fixed_speed(self, channel: str, duty: int, **_: Any) -> None:
        targets = self.channels if channel == "fan" else [channel]
        for name in targets:
            path = self._pwm.get(name)
            if path is None:
                raise ValueError(f"unknown channel {name}")
            enable = self._enable.get(name)
            if enable is not None and _read_int(enable) != ENABLE_MANUAL:
                try:
                    _write_int(enable, ENABLE_MANUAL)
                except OSError as exc:
                    raise PermissionError(
                        f"cannot switch {name} to manual control ({exc}). Mainboard fans "
                        "need root - run the corsair-controld service."
                    ) from exc
            try:
                _write_int(path, int(round(max(0, min(100, duty)) * 255 / 100)))
            except OSError as exc:
                raise PermissionError(
                    f"cannot write {path} ({exc}). Mainboard fans need root - run the "
                    "corsair-controld service."
                ) from exc

    def set_speed_profile(self, channel: str, profile: Sequence[tuple[int, int]], **_: Any) -> None:
        raise NotImplementedError("hwmon has no hardware curves")

    # ------------------------------------------------------------------
    def restore_automatic(self) -> None:
        """Hand the fans back to the mainboard's own controller."""
        for channel, path in self._enable.items():
            value = self._original_enable.get(channel, ENABLE_AUTOMATIC)
            try:
                _write_int(path, value)
            except OSError as exc:  # pragma: no cover - depends on privileges
                log.debug("Cannot restore %s: %s", path, exc)


def discover_hwmon_backends(*, include_all: bool = False) -> list[HwmonBackend]:
    """Find every hwmon chip that exposes a PWM output."""
    backends: list[HwmonBackend] = []
    if not HWMON_ROOT.exists():
        return backends

    for directory in sorted(HWMON_ROOT.glob("hwmon*")):
        chip = _read(directory / "name") or directory.name
        if not include_all and chip.lower() in SKIP_CHIPS:
            log.debug("Skipping %s - already handled over USB", chip)
            continue
        backend = HwmonBackend(directory, chip)
        if backend.channels:
            backends.append(backend)
    return backends
