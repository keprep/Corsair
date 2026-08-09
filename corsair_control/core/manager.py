"""Device discovery."""

from __future__ import annotations

import logging
from typing import Iterable

from corsair_control.core.device import DeviceError, ManagedDevice, demo_devices
from corsair_control.core.hwmon import discover_hwmon_backends

log = logging.getLogger(__name__)

#: USB vendor ID used by Corsair peripherals and cooling gear.
CORSAIR_VENDOR_ID = 0x1B1C


class DiscoveryResult:
    def __init__(self) -> None:
        self.devices: list[ManagedDevice] = []
        self.errors: list[str] = []
        self.skipped: list[str] = []

    def __bool__(self) -> bool:
        return bool(self.devices)


def _iter_backends() -> Iterable[object]:
    from liquidctl import find_liquidctl_devices

    return find_liquidctl_devices()


def discover(
    *,
    demo: bool = False,
    corsair_only: bool = True,
    initialize: bool = True,
    include_hwmon: bool = True,
) -> DiscoveryResult:
    """Find supported devices and bring them up.

    ``corsair_only`` keeps the device list focused on what this application is
    named after; other liquidctl-supported hardware still works and can be
    included by passing ``False``. ``include_hwmon`` adds mainboard fan
    headers exposed by the kernel.
    """
    result = DiscoveryResult()

    if demo:
        for device in demo_devices():
            device.connect()
            device.initialize()
            device.probe()
            result.devices.append(device)
        return result

    if include_hwmon:
        result.devices.extend(_discover_hwmon(result))

    try:
        backends = list(_iter_backends())
    except ImportError:
        result.errors.append(
            "liquidctl is not installed. Install it with 'pip install liquidctl'."
        )
        return result
    except Exception as exc:
        result.errors.append(f"Device enumeration failed: {exc}")
        return result

    if not backends:
        result.errors.append(
            "No supported USB device found. Check the connection and whether the "
            "udev rules are installed."
        )
        return result

    for backend in backends:
        vendor = getattr(backend, "vendor_id", None)
        description = getattr(backend, "description", str(backend))
        if corsair_only and vendor is not None and vendor != CORSAIR_VENDOR_ID:
            result.skipped.append(description)
            continue

        device = ManagedDevice(backend)
        try:
            device.connect()
            if initialize:
                device.initialize()
            device.probe()
        except DeviceError as exc:
            log.warning("Cannot use %s: %s", description, exc)
            result.errors.append(f"{description}: {exc}")
            device.disconnect()
            continue
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Unexpected failure on %s: %s", description, exc)
            result.errors.append(f"{description}: {exc}")
            device.disconnect()
            continue

        if not device.channels:
            result.skipped.append(f"{description} (no fan or pump channel)")
            device.disconnect()
            continue

        result.devices.append(device)

    if not result.devices and not result.errors:
        result.errors.append("Devices were found, but none of them exposes a fan or pump channel.")

    return result


def _discover_hwmon(result: DiscoveryResult) -> list[ManagedDevice]:
    """Wrap every hwmon chip with PWM outputs as a device."""
    devices: list[ManagedDevice] = []
    try:
        backends = discover_hwmon_backends()
    except Exception as exc:  # pragma: no cover - defensive
        result.errors.append(f"hwmon scan failed: {exc}")
        return devices

    for backend in backends:
        device = ManagedDevice(backend)
        try:
            device.connect()
            device.initialize()
            device.probe()
        except DeviceError as exc:
            result.errors.append(f"{backend.description}: {exc}")
            continue

        writable = backend.any_writable
        for channel in device.channels:
            channel.controllable = backend.writable(channel.channel_id)
        if not writable:
            result.errors.append(
                f"{backend.description}: mainboard fans are visible but not writable. "
                "Run the corsair-controld service as root to control them."
            )
        devices.append(device)
    return devices


def release(devices: Iterable[ManagedDevice]) -> None:
    for device in devices:
        device.disconnect()
