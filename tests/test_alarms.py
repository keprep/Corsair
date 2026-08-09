import time

from corsair_control.core.alarms import (
    KIND_DEVICE_ERROR,
    KIND_FAN_STALLED,
    KIND_PUMP_SLOW,
    AlarmMonitor,
    AlarmSettings,
)
from corsair_control.core.engine import ChannelSnapshot, DeviceSnapshot, Snapshot


def channel(channel_id="fan1", kind="fan", rpm=1000.0, duty=50.0):
    return ChannelSnapshot(
        device_key="dev",
        channel_id=channel_id,
        label=channel_id,
        kind=kind,
        controllable=True,
        mode="curve",
        rpm=rpm,
        duty=duty,
        target_duty=duty,
        sensor_id=None,
        sensor_label="",
        sensor_value=None,
    )


def snapshot(channels, error=None, sensors=None):
    return Snapshot(
        timestamp=time.time(),
        devices=(
            DeviceSnapshot(
                key="dev",
                name="Demo",
                description="Demo",
                driver="Demo",
                bus_info="",
                temperatures={},
                channels=tuple(channels),
                error=error,
            ),
        ),
        sensors=sensors or {},
    )


def monitor(**kwargs):
    return AlarmMonitor(settings=AlarmSettings(debounce_seconds=0.0, **kwargs))


def test_nothing_fires_when_everything_is_fine():
    raised, cleared = monitor().evaluate(snapshot([channel()]))
    assert raised == [] and cleared == []


def test_slow_pump_is_critical():
    raised, _ = monitor().evaluate(snapshot([channel("pump", kind="pump", rpm=200)]))
    assert len(raised) == 1
    assert raised[0].kind == KIND_PUMP_SLOW
    assert raised[0].severity == "critical"


def test_healthy_pump_is_quiet():
    raised, _ = monitor().evaluate(snapshot([channel("pump", kind="pump", rpm=2100)]))
    assert raised == []


def test_stalled_fan_is_detected():
    raised, _ = monitor().evaluate(snapshot([channel(rpm=0, duty=60)]))
    assert raised[0].kind == KIND_FAN_STALLED


def test_fan_at_low_duty_may_stand_still():
    raised, _ = monitor().evaluate(snapshot([channel(rpm=0, duty=5)]))
    assert raised == []


def test_device_error_wins_over_channel_alarms():
    raised, _ = monitor().evaluate(snapshot([channel(rpm=0, duty=60)], error="gone"))
    assert [alarm.kind for alarm in raised] == [KIND_DEVICE_ERROR]


def test_debounce_delays_the_alarm():
    watcher = AlarmMonitor(settings=AlarmSettings(debounce_seconds=60.0))
    raised, _ = watcher.evaluate(snapshot([channel("pump", kind="pump", rpm=10)]))
    assert raised == []
    assert watcher.active == []


def test_alarm_clears_when_the_condition_goes_away():
    watcher = monitor()
    watcher.evaluate(snapshot([channel("pump", kind="pump", rpm=10)]))
    assert watcher.active
    _raised, cleared = watcher.evaluate(snapshot([channel("pump", kind="pump", rpm=2000)]))
    assert len(cleared) == 1
    assert watcher.active == []


def test_alarm_is_only_raised_once():
    watcher = monitor()
    first, _ = watcher.evaluate(snapshot([channel("pump", kind="pump", rpm=10)]))
    second, _ = watcher.evaluate(snapshot([channel("pump", kind="pump", rpm=10)]))
    assert len(first) == 1 and second == []


def test_temperature_warning():
    watcher = monitor(temperature_warning=80.0)
    raised, _ = watcher.evaluate(snapshot([channel()], sensors={"cpu": 90.0}))
    assert any(alarm.kind == "temperature" for alarm in raised)


def test_disabling_clears_everything():
    watcher = monitor()
    watcher.evaluate(snapshot([channel("pump", kind="pump", rpm=10)]))
    watcher.settings.enabled = False
    raised, cleared = watcher.evaluate(snapshot([channel("pump", kind="pump", rpm=10)]))
    assert raised == [] and len(cleared) == 1
    assert watcher.active == []
