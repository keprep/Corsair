"""Device discovery."""

from __future__ import annotations

import logging
from typing import Iterable

from corsair_control.core.device import DeviceError, ManagedDevice, demo_devices
from corsair_control.core.experimental import parse_bindings, probe
from corsair_control.core.hwmon import discover_hwmon_backends

log = logging.getLogger(__name__)

#: USB vendor ID used by Corsair peripherals and cooling gear.
CORSAIR_VENDOR_ID = 0x1B1C


class DiscoveryResult:
    def __init__(self) -> None:
        self.devices: list[ManagedDevice] = []
        self.errors: list[str] = []
        self.skipped: list[str] = []
        #: Corsair hardware present on the bus that no driver claimed, as
        #: (vendor_id, product_id, product_name) - see :func:`unclaimed_corsair`.
        self.unclaimed: list[tuple[int, int, str]] = []

    def __bool__(self) -> bool:
        return bool(self.devices)


def unclaimed_corsair(claimed: Iterable[ManagedDevice]) -> list[tuple[int, int, str]]:
    """Corsair USB devices that liquidctl has no driver for.

    Newer revisions ship new product IDs - the iCUE ELITE CAPELLIX XT is the
    current example - and liquidctl simply does not see them. Saying "there is
    a Corsair device here that no driver claims, its ID is 1b1c:xxxx" turns a
    silent absence into something the user can act on and report upstream.
    """
    try:
        import hid
    except ImportError:  # pragma: no cover - hidapi ships with liquidctl
        return []

    taken = set()
    for device in claimed:
        product = getattr(device, "_dev", None)
        pid = getattr(product, "product_id", None)
        if pid is not None:
            taken.add(int(pid))

    found: dict[int, str] = {}
    try:
        for entry in hid.enumerate(CORSAIR_VENDOR_ID, 0):
            pid = int(entry.get("product_id", 0))
            if pid in taken or pid in found:
                continue
            found[pid] = str(entry.get("product_string") or "").strip() or "unknown model"
    except Exception as exc:  # pragma: no cover - depends on permissions
        log.debug("Cannot enumerate HID devices: %s", exc)
        return []

    return [(CORSAIR_VENDOR_ID, pid, name) for pid, name in sorted(found.items())]


def _iter_backends() -> Iterable[object]:
    from liquidctl import find_liquidctl_devices

    return find_liquidctl_devices()


def discover(
    *,
    demo: bool = False,
    corsair_only: bool = True,
    initialize: bool = True,
    include_hwmon: bool = True,
    bindings: Iterable[str] | None = None,
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

    result.devices.extend(_apply_bindings(bindings, result))
    result.unclaimed = unclaimed_corsair(result.devices)
    for vendor, product, name in result.unclaimed:
        result.errors.append(
            f"Corsair device {vendor:04x}:{product:04x} ({name}) is connected but no "
            "liquidctl driver claims it - this model is not supported yet. It can be "
            "bound to a driver by hand (Settings > unsupported devices), and reporting "
            "the ID at github.com/liquidctl/liquidctl helps."
        )

    return result


def _apply_bindings(bindings: Iterable[str] | None, result: DiscoveryResult) -> list[ManagedDevice]:
    """Bring up devices the user bound to a driver by hand."""
    if not bindings:
        return []

    parsed, errors = parse_bindings(bindings)
    result.errors.extend(errors)

    claimed = {getattr(getattr(d, "_dev", None), "product_id", None) for d in result.devices}
    devices: list[ManagedDevice] = []

    for binding in parsed:
        if binding.product_id in claimed:
            log.info("%s is already handled by a real driver, skipping the binding", binding)
            continue

        backend, outcome = probe(binding)
        if backend is None:
            result.errors.append(f"Forced binding {binding} did not work: {outcome.describe()}")
            continue

        device = ManagedDevice(backend, experimental=True)
        try:
            device.probe()
        except DeviceError as exc:
            result.errors.append(f"Forced binding {binding} failed while probing: {exc}")
            device.disconnect()
            continue

        if not device.channels:
            result.errors.append(
                f"Forced binding {binding} answered, but exposes no fan or pump channel."
            )
            device.disconnect()
            continue

        log.warning("Using %s through a forced binding - this is experimental", binding)
        devices.append(device)
    return devices


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
