import pytest

from corsair_control.core.automation import Rule
from corsair_control.core.config import Settings
from corsair_control.core.curve import CurvePoint, FanCurve
from corsair_control.core.device import DeviceError
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


def test_channel_can_follow_two_sensors(engine):
    device = engine.devices[0]
    config = engine.store.active.device(device.key).channel("fan1")
    config.min_duty = 0
    config.max_duty = 100
    config.allow_zero_rpm = True
    config.curve = FanCurve([CurvePoint(0, 0), CurvePoint(100, 100)])
    config.set_sensor_ids(["demo:cpu", "demo:gpu"])

    snapshot = engine.tick()
    channel = next(
        c for c in snapshot.devices[0].channels if c.channel_id == "fan1"
    )
    hottest = max(engine.sensors.value("demo:cpu"), engine.sensors.value("demo:gpu"))
    assert channel.sensor_value == hottest
    assert "+" in channel.sensor_label


def test_automation_switches_the_profile(engine):
    engine.store.rules = [Rule(name="hot", profile="Performance", temperature_above=0.0)]
    engine.automation.rules = engine.store.rules
    engine.automation.enabled = True

    engine.tick()
    assert engine.store.active_name == "Performance"
    assert engine.snapshot.automation_rule == "hot"


def test_automation_stays_out_of_the_way_when_disabled(engine):
    engine.store.rules = [Rule(name="hot", profile="Performance", temperature_above=0.0)]
    engine.automation.rules = engine.store.rules
    engine.automation.enabled = False

    before = engine.store.active_name
    engine.tick()
    assert engine.store.active_name == before


def test_alarms_reach_the_snapshot(engine):
    engine.alarms.settings.debounce_seconds = 0.0
    engine.alarms.settings.pump_minimum_rpm = 99_000

    snapshot = engine.tick()
    assert any(alarm.kind == "pump_slow" for alarm in snapshot.alarms)
    assert snapshot.new_alarms


def test_calibration_excludes_the_channel_from_control(engine):
    device = engine.devices[0]
    config = engine.store.active.device(device.key).channel("fan1")
    config.mode = MODE_FIXED
    config.fixed_duty = 80
    config.min_duty = 0

    engine._calibrating.add((device.key, "fan1"))
    device._applied.clear()
    engine.tick()
    assert device.applied_duty("fan1") is None

    engine._calibrating.clear()
    engine.tick()
    assert device.applied_duty("fan1") == 80


def test_calibration_stores_the_result_and_raises_the_floor(engine):
    device = engine.devices[0]
    result = engine.calibrate(
        device.key, "fan1", settle_seconds=0.0, samples=1, sample_interval=0.0
    )

    config = engine.store.active.device(device.key).channel("fan1")
    assert config.calibration is not None
    assert config.calibration.max_rpm > 0
    if result.stall_duty is not None:
        assert config.min_duty >= result.stall_duty


def test_calibration_rejects_an_unknown_channel(engine):
    with pytest.raises(DeviceError):
        engine.calibrate(engine.devices[0].key, "fan9")
