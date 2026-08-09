import pytest

from corsair_control.core.config import Settings
from corsair_control.core.curve import CurvePoint, FanCurve
from corsair_control.core.engine import ControlEngine
from corsair_control.core.profile import MODE_FIXED, MODE_MANUAL, ProfileStore


@pytest.fixture
def engine(tmp_path):
    settings = Settings(poll_interval=0.5, emergency_temperature=90.0)
    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    engine = ControlEngine(settings, store, demo=True)
    engine.discover()
    yield engine
    engine.stop()


def test_discovery_finds_demo_devices(engine):
    assert len(engine.devices) == 2
    assert engine.startup_errors == []


def test_every_channel_gets_a_config(engine):
    profile = engine.store.active
    for device in engine.devices:
        config = profile.device(device.key)
        assert set(config.channels) == {c.channel_id for c in device.channels}


def test_aio_channels_default_to_the_liquid_sensor(engine):
    aio = engine.devices[0]
    config = engine.store.active.device(aio.key)
    sensor_id = config.channels["fan1"].sensor_id
    assert sensor_id is not None and sensor_id.startswith("dev:")


def test_tick_produces_a_snapshot(engine):
    snapshot = engine.tick()
    assert snapshot.devices
    assert snapshot.profile == engine.store.active_name
    channel = snapshot.devices[0].channels[0]
    assert channel.rpm is not None


def test_fixed_mode_writes_the_configured_duty(engine):
    device = engine.devices[0]
    config = engine.store.active.device(device.key).channel("fan1")
    config.mode = MODE_FIXED
    config.fixed_duty = 73
    config.min_duty = 0

    engine.tick()
    assert device.applied_duty("fan1") == 73


def test_manual_mode_never_writes(engine):
    device = engine.devices[0]
    config = engine.store.active.device(device.key).channel("fan2")
    config.mode = MODE_MANUAL

    for _ in range(3):
        engine.tick()
    assert device.applied_duty("fan2") is None


def test_curve_mode_follows_the_temperature(engine):
    device = engine.devices[0]
    config = engine.store.active.device(device.key).channel("fan1")
    config.min_duty = 0
    config.max_duty = 100
    config.curve = FanCurve([CurvePoint(0, 0), CurvePoint(100, 100)])
    config.allow_zero_rpm = True

    engine.tick()
    first = device.applied_duty("fan1")
    assert first is not None

    # A curve that maps 1:1 must land within a few points of the sensor value.
    temp = engine.sensors.value(config.sensor_id)
    assert abs(first - temp) < 12


def test_emergency_forces_full_speed(engine):
    engine.settings.emergency_temperature = 0.0
    engine.tick()
    for device in engine.devices:
        for channel in device.channels:
            if channel.controllable:
                assert device.applied_duty(channel.channel_id) == 100


def test_override_beats_the_curve(engine):
    device = engine.devices[0]
    engine.store.active.device(device.key).channel("fan1").min_duty = 0
    engine.set_override(device.key, "fan1", 12)
    engine.tick()
    assert device.applied_duty("fan1") == 12

    engine.set_override(device.key, "fan1", None)
    engine.tick()
    assert device.applied_duty("fan1") != 12


def test_pause_stops_writing(engine):
    device = engine.devices[0]
    engine.tick()
    engine.pause(True)
    device._applied.clear()
    engine.tick()
    assert device.applied_duty("fan1") is None


def test_dry_run_does_not_touch_hardware(tmp_path):
    settings = Settings(poll_interval=0.5)
    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    engine = ControlEngine(settings, store, demo=True, dry_run=True)
    engine.discover()
    try:
        engine.tick()
        device = engine.devices[0]
        assert device.applied_duty("fan1") is None
    finally:
        engine.stop()


def test_profile_switch_changes_the_target(engine):
    device = engine.devices[0]
    engine.set_profile("Silent")
    engine.tick()
    silent = device.applied_duty("fan1")

    engine.set_profile("Performance")
    engine.tick()
    performance = device.applied_duty("fan1")

    assert performance is not None and silent is not None
    assert performance > silent
