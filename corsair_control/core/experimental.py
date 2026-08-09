"""Binding unrecognised Corsair devices to a driver by hand.

liquidctl matches on (vendor, product) pairs. When Corsair ships a revision
with a new product ID - the iCUE ELITE CAPELLIX XT being the current example,
where the request for support was closed as not planned - the device becomes
invisible even though it usually speaks a protocol liquidctl already
implements.

This module lets the user say "treat 1b1c:0c40 as a Commander Core" and makes
that stick. It is deliberately manual:

* nothing is ever bound automatically - a binding only exists because someone
  wrote it down;
* before any control is offered, the device is probed and its answer has to
  look like a cooler. Corsair's vendor ID also covers keyboards, mice and
  headsets, and none of those should be sent fan-control packets;
* a device bound this way is flagged, so the UI can say what it is.

It can still fail. The honest expectation is "either it works or the device
ignores us", not "this is supported".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

log = logging.getLogger(__name__)

BINDING_RE = re.compile(
    r"^\s*(?:(?P<vendor>[0-9a-f]{4}):)?(?P<product>[0-9a-f]{4})\s*=\s*(?P<protocol>[\w.-]+)\s*$",
    re.IGNORECASE,
)

CORSAIR_VENDOR_ID = 0x1B1C


@dataclass(frozen=True)
class Protocol:
    """One way of talking to a device, as implemented by a liquidctl driver."""

    key: str
    label: str
    driver: str  # "module.ClassName" inside liquidctl.driver
    kwargs: dict[str, Any]
    note: str = ""


#: The protocols worth offering. Each mirrors an entry liquidctl already has
#: for a closely related model, so the packet format is known-good.
PROTOCOLS: dict[str, Protocol] = {
    "commander-core-aio": Protocol(
        key="commander-core-aio",
        label="Commander Core (AIO with pump)",
        driver="commander_core.CommanderCore",
        kwargs={"has_pump": True},
        note="iCUE ELITE CAPELLIX and its XT revision, H100i/H115i/H150i",
    ),
    "commander-core-hub": Protocol(
        key="commander-core-hub",
        label="Commander Core XT (fan hub, no pump)",
        driver="commander_core.CommanderCore",
        kwargs={"has_pump": False},
        note="Commander Core XT and Commander ST",
    ),
    "commander-pro": Protocol(
        key="commander-pro",
        label="Commander Pro (6 fans, 4 probes)",
        driver="commander_pro.CommanderPro",
        kwargs={"fan_count": 6, "temp_probs": 4, "led_channels": 2},
    ),
    "hydro-platinum-2": Protocol(
        key="hydro-platinum-2",
        label="Hydro Platinum / Pro XT (2 fans)",
        driver="hydro_platinum.HydroPlatinum",
        kwargs={"fan_count": 2, "fan_leds": 0},
    ),
    "hydro-platinum-3": Protocol(
        key="hydro-platinum-3",
        label="Hydro Platinum / Pro XT (3 fans)",
        driver="hydro_platinum.HydroPlatinum",
        kwargs={"fan_count": 3, "fan_leds": 0},
    ),
}


@dataclass(frozen=True)
class Binding:
    vendor_id: int
    product_id: int
    protocol: str

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"

    def __str__(self) -> str:
        return f"{self.usb_id}={self.protocol}"

    @classmethod
    def parse(cls, text: str) -> "Binding":
        """Parse ``1b1c:0c40=commander-core-aio`` (vendor optional)."""
        match = BINDING_RE.match(text or "")
        if not match:
            raise ValueError(
                f"cannot read '{text}' - expected something like "
                "'1b1c:0c40=commander-core-aio'"
            )
        protocol = match.group("protocol").lower()
        if protocol not in PROTOCOLS:
            known = ", ".join(sorted(PROTOCOLS))
            raise ValueError(f"unknown protocol '{protocol}' - pick one of: {known}")
        vendor = match.group("vendor")
        return cls(
            vendor_id=int(vendor, 16) if vendor else CORSAIR_VENDOR_ID,
            product_id=int(match.group("product"), 16),
            protocol=protocol,
        )


def parse_bindings(entries: Iterable[str]) -> tuple[list[Binding], list[str]]:
    """Parse many, collecting errors instead of raising on the first bad one."""
    bindings: list[Binding] = []
    errors: list[str] = []
    for entry in entries or ():
        try:
            bindings.append(Binding.parse(str(entry)))
        except ValueError as exc:
            errors.append(str(exc))
    return bindings, errors


# ----------------------------------------------------------------------
# construction
# ----------------------------------------------------------------------
def _driver_class(protocol: Protocol):
    import importlib

    module_name, class_name = protocol.driver.rsplit(".", 1)
    module = importlib.import_module(f"liquidctl.driver.{module_name}")
    return getattr(module, class_name)


def build_backends(binding: Binding) -> list[Any]:
    """Construct a driver over every HID interface of the given USB ID.

    A device often exposes several interfaces and only one of them answers, so
    all candidates are returned and the caller probes them in turn.
    """
    protocol = PROTOCOLS.get(binding.protocol)
    if protocol is None:
        return []

    try:
        import hid
        from liquidctl.driver.usb import HidapiDevice
    except ImportError as exc:  # pragma: no cover - liquidctl is a dependency
        log.warning("Cannot bind %s: %s", binding, exc)
        return []

    try:
        driver = _driver_class(protocol)
    except (ImportError, AttributeError) as exc:
        log.warning("Cannot load the driver for %s: %s", binding, exc)
        return []

    description = f"{protocol.label} @ {binding.usb_id} (forced)"
    backends = []
    try:
        handles = list(HidapiDevice.enumerate(hid, binding.vendor_id, binding.product_id))
    except Exception as exc:  # pragma: no cover - depends on permissions
        log.warning("Cannot enumerate %s: %s", binding.usb_id, exc)
        return []

    for handle in handles:
        try:
            backends.append(driver(handle, description, **protocol.kwargs))
        except Exception as exc:
            log.debug("Cannot construct %s over %s: %s", protocol.driver, binding.usb_id, exc)
    return backends


# ----------------------------------------------------------------------
# safety probe
# ----------------------------------------------------------------------
@dataclass
class ProbeResult:
    ok: bool
    reason: str
    status: list[tuple] = None  # type: ignore[assignment]
    speeds: int = 0
    temperatures: int = 0

    def __post_init__(self) -> None:
        if self.status is None:
            self.status = []

    def describe(self) -> str:
        if not self.ok:
            return self.reason
        return f"{self.speeds} speed reading(s), {self.temperatures} temperature(s)"


SPEED_HINT = re.compile(r"(fan|pump).*speed|speed.*(fan|pump)", re.IGNORECASE)


def looks_like_a_cooler(status: Iterable[tuple]) -> tuple[bool, str, int, int]:
    """Decide whether a status report came from cooling hardware.

    The gate that keeps a keyboard from being driven like a fan hub: a device
    that reports neither a speed nor a temperature is not one we control.
    """
    speeds = temperatures = 0
    for entry in status or ():
        try:
            key, value, unit = entry[0], entry[1], entry[2] if len(entry) > 2 else ""
        except (TypeError, IndexError):
            continue
        if not isinstance(value, (int, float)):
            continue
        if str(unit).strip() == "rpm" or SPEED_HINT.search(str(key)):
            speeds += 1
        elif str(unit).strip() in {"°C", "C", "degC"}:
            temperatures += 1

    if speeds == 0 and temperatures == 0:
        return False, "the device answered, but reported no speed and no temperature", 0, 0
    return True, "", speeds, temperatures


def probe(binding: Binding) -> tuple[Any | None, ProbeResult]:
    """Try a binding. Returns the working backend and what was seen.

    Connecting and initialising does write to the device - there is no way to
    read a Commander Core without waking it - so this is the point of no
    return, and why nothing calls it without the user asking.
    """
    backends = build_backends(binding)
    if not backends:
        return None, ProbeResult(False, f"no device found at {binding.usb_id}")

    last_reason = "the device did not answer"
    for backend in backends:
        try:
            backend.connect()
        except Exception as exc:
            last_reason = f"cannot open the device: {exc}"
            continue
        try:
            backend.initialize()
            status = list(backend.get_status() or ())
        except Exception as exc:
            last_reason = f"the device rejected the protocol: {exc}"
            try:
                backend.disconnect()
            except Exception:
                pass
            continue

        ok, reason, speeds, temperatures = looks_like_a_cooler(status)
        if not ok:
            last_reason = reason
            try:
                backend.disconnect()
            except Exception:
                pass
            continue

        return backend, ProbeResult(True, "", status, speeds, temperatures)

    return None, ProbeResult(False, last_reason)
