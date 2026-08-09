import pytest

from corsair_control.core.device import DeviceError, ManagedDevice, _DemoBackend, demo_devices


@pytest.fixture
def device():
    dev = ManagedDevice(_DemoBackend(), demo=True)
    dev.connect()
    dev.initialize()
    dev.probe()
    return dev


def test_channels_are_discovered(device):
    ids = {c.channel_id for c in device.channels}
    assert ids == {"fan1", "fan2", "fan3", "pump"}
    assert device.channels[0].channel_id == "pump"  # pump is listed first


def test_status_is_split_into_temps_speeds_and_duties(device):
    status = device.refresh()
    assert "Liquid temperature" in status.temperatures
    assert status.speeds["fan1"] > 0
    assert "fan1" in status.duties
    assert status.extras["Firmware version"] == "2.10.219"
    assert status.ok


def test_set_duty_reaches_the_backend(device):
    device.set_duty("fan1", 88)
    assert device.applied_duty("fan1") == 88
    status = device.refresh()
    assert status.duties["fan1"] == 88


def test_set_duty_is_clamped(device):
    device.set_duty("fan1", 300)
    assert device.applied_duty("fan1") == 100


def test_unknown_channel_raises_device_error(device):
    with pytest.raises(DeviceError):
        device.set_duty("fan9", 50)


def test_pump_modes_are_detected(device):
    assert device.pump_modes == ["quiet", "balanced", "extreme"]


def test_lighting_is_detected(device):
    assert device.supports_lighting
    assert device.lighting_channels[0].channel_id == "led"
    assert "breathing" in device.lighting_channels[0].modes


def test_pump_modes_are_hidden_when_there_is_no_pump():
    # The demo Commander shares the base class (which accepts pump_mode) but
    # has no pump channel, so the mode must not be advertised.
    commander = demo_devices()[1]
    commander.connect()
    commander.initialize()
    commander.probe()
    assert commander.channels and not any(c.is_pump for c in commander.channels)
    assert commander.pump_modes == []


def test_keys_are_unique_per_device():
    devices = demo_devices()
    assert len({d.key for d in devices}) == len(devices)


def test_temperature_sensor_ids_are_namespaced(device):
    device.refresh()
    ids = [sid for sid, _ in device.temperature_sensors()]
    assert ids == [f"dev:{device.key}:Liquid temperature"]


def test_short_name_drops_the_vendor_prefix(device):
    assert device.short_name.startswith("Hydro")
