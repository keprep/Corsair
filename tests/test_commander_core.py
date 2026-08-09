"""Regression tests for the Commander Core family.

The iCUE H150i ELITE CAPELLIX and the Commander Core XT both speak through
liquidctl's ``commander_core`` driver, which differs from the Hydro Platinum
one in three ways that each broke something here:

* it labels fans ``Fan speed 1``, not ``Fan 1 speed``;
* it calls the loop ``Water temperature``, not ``Liquid temperature``;
* it inherits ``set_color`` and ``set_screen`` that only raise, so a plain
  ``hasattr`` claims lighting and an LCD that the hardware does not have.

The fakes below copy those exact labels from liquidctl 1.16.
"""

from typing import Any

import pytest

from corsair_control.core.config import Settings
from corsair_control.core.device import ManagedDevice, _always_raises_unsupported
from corsair_control.core.engine import ControlEngine
from corsair_control.core.profile import ProfileStore


class NotSupportedByDriver(Exception):
    pass


class _CommanderCoreLike:
    """Shaped like liquidctl.driver.commander_core.CommanderCore."""

    vendor_id = 0x1B1C
    bus = "hid"

    def __init__(self, *, has_pump: bool, description: str, address: str) -> None:
        self._has_pump = has_pump
        self.description = description
        self.address = address
        self.product_id = 0x0C1C if has_pump else 0x0C2A
        self._duties: dict[str, float] = {}

    def connect(self, **_: Any) -> None:
        return None

    def disconnect(self, **_: Any) -> None:
        return None

    def initialize(self, **_: Any) -> list:
        return [("Firmware version", "2.10.219", "")]

    def get_status(self, **_: Any) -> list[tuple[str, Any, str]]:
        status: list[tuple[str, Any, str]] = []
        speeds = [2400, 900, 910, 905, 0, 0, 0] if self._has_pump else [800] * 6
        for index, speed in enumerate(speeds):
            if self._has_pump:
                label = "Pump speed" if index == 0 else f"Fan speed {index}"
            else:
                label = f"Fan speed {index + 1}"
            status.append((label, speed, "rpm"))
        if self._has_pump:
            status.append(("Water temperature", 31.4, "°C"))
        else:
            status.append(("Temperature 1", 28.0, "°C"))
        return status

    def set_fixed_speed(self, channel: str, duty: int, **_: Any) -> None:
        self._duties[channel] = float(duty)

    def set_speed_profile(self, channel: str, profile, **_: Any) -> None:
        return None

    def set_color(self, channel, mode, colors, **kwargs):
        """Not supported by this driver."""
        raise NotSupportedByDriver

    def set_screen(self, channel, mode, value, **kwargs):
        """Not supported by this driver."""
        raise NotSupportedByDriver


def build(has_pump: bool) -> ManagedDevice:
    backend = _CommanderCoreLike(
        has_pump=has_pump,
        description="Corsair Commander Core (broken)"
        if has_pump
        else "Corsair Commander Core XT (broken)",
        address="core0" if has_pump else "core1",
    )
    device = ManagedDevice(backend)
    device.connect()
    device.initialize()
    device.probe()
    return device


# ----------------------------------------------------------------------
# channel discovery
# ----------------------------------------------------------------------
def test_aio_exposes_pump_and_every_fan():
    device = build(has_pump=True)
    ids = {c.channel_id for c in device.channels}
    assert "pump" in ids
    # "Fan speed 1".."Fan speed 6" must all be found.
    assert {"fan1", "fan2", "fan3", "fan4", "fan5", "fan6"} <= ids


def test_fan_hub_without_pump_is_not_empty():
    """The regression that would have hidden every fan on a Commander Core XT."""
    device = build(has_pump=False)
    ids = {c.channel_id for c in device.channels}
    assert ids == {"fan1", "fan2", "fan3", "fan4", "fan5", "fan6"}
    assert not any(c.is_pump for c in device.channels)


def test_speeds_are_parsed_from_the_trailing_index():
    device = build(has_pump=True)
    status = device.refresh()
    assert status.speeds["pump"] == 2400
    assert status.speeds["fan1"] == 900
    assert status.speeds["fan2"] == 910


def test_pump_channel_gets_a_floor():
    device = build(has_pump=True)
    pump = next(c for c in device.channels if c.is_pump)
    assert pump.default_floor == 50.0


# ----------------------------------------------------------------------
# capabilities that only look present
# ----------------------------------------------------------------------
def test_lighting_is_not_advertised_when_set_color_only_raises():
    assert build(has_pump=True).supports_lighting is False


def test_screen_is_not_advertised_when_set_screen_only_raises():
    assert build(has_pump=True).supports_screen is False


def test_detection_helper_reads_the_method_body():
    assert _always_raises_unsupported(_CommanderCoreLike.set_color) is True
    assert _always_raises_unsupported(_CommanderCoreLike.set_fixed_speed) is False


def test_detection_helper_fails_safe_on_unreadable_source():
    # A builtin has no retrievable source; assuming "supported" keeps the
    # feature reachable instead of silently hiding it.
    assert _always_raises_unsupported(len) is False


def test_hardware_curves_stay_available():
    assert build(has_pump=True).supports_hardware_curves is True


# ----------------------------------------------------------------------
# the loop temperature
# ----------------------------------------------------------------------
def test_water_temperature_is_read():
    device = build(has_pump=True)
    assert device.refresh().temperatures["Water temperature"] == pytest.approx(31.4)


def test_engine_picks_water_temperature_as_the_default_source(tmp_path, monkeypatch):
    from corsair_control.core import manager

    device = build(has_pump=True)
    monkeypatch.setattr(manager, "discover", lambda **kwargs: _Result([device]))
    monkeypatch.setattr("corsair_control.core.engine.discover", lambda **kwargs: _Result([device]))

    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    engine = ControlEngine(Settings(poll_interval=0.5), store)
    try:
        engine.discover()
        sensor_id = f"dev:{device.key}:Water temperature"
        assert engine.sensors.get(sensor_id) is not None
        assert engine.sensors.get(sensor_id).category == "liquid"

        config = store.active.device(device.key).channel("fan1")
        assert config.sensor_id == sensor_id
    finally:
        engine.stop()


class _Result:
    def __init__(self, devices):
        self.devices = devices
        self.errors: list[str] = []
        self.skipped: list[str] = []
