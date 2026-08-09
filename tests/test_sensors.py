from corsair_control.core.sensors import Sensor, SensorHub, discover_hwmon


def constant(value):
    return lambda: value


def test_hub_polls_and_keeps_history():
    hub = SensorHub(history_seconds=60, poll_interval=1.0)
    hub.register([Sensor("a", "A", "cpu", constant(42.0))])

    hub.poll()
    hub.poll()

    assert hub.value("a") == 42.0
    assert len(hub.history("a")) == 2


def test_failing_sensor_does_not_break_the_poll():
    def boom():
        raise OSError("gone")

    hub = SensorHub()
    hub.register(
        [
            Sensor("bad", "Bad", "cpu", boom),
            Sensor("good", "Good", "cpu", constant(30.0)),
        ]
    )
    values = hub.poll()
    assert values == {"good": 30.0}


def test_virtual_max_sensor_aggregates():
    hub = SensorHub()
    hub.register(
        [
            Sensor("cpu0", "Core 0", "cpu", constant(50.0)),
            Sensor("cpu1", "Core 1", "cpu", constant(70.0)),
        ]
    )
    hub._register_virtual()
    hub.poll()
    assert hub.value("virtual:max:cpu") == 70.0


def test_hottest_ignores_uninteresting_categories():
    hub = SensorHub()
    hub.register(
        [
            Sensor("cpu0", "Core 0", "cpu", constant(50.0)),
            Sensor("ssd", "SSD", "storage", constant(95.0)),
        ]
    )
    hub.poll()
    assert hub.hottest() == 50.0


def test_default_sensor_prefers_cpu():
    hub = SensorHub()
    hub.register(
        [
            Sensor("gpu0", "GPU", "gpu", constant(60.0)),
            Sensor("cpu0", "CPU", "cpu", constant(50.0)),
        ]
    )
    assert hub.default_sensor_id() == "cpu0"


def test_demo_sensors_are_registered():
    hub = SensorHub()
    hub.discover(demo=True)
    values = hub.poll()
    assert set(values) == {"demo:cpu", "demo:gpu"}


def test_hwmon_discovery_survives_a_missing_sysfs():
    # On a container without /sys/class/hwmon this must simply return nothing.
    assert isinstance(discover_hwmon(), list)


def test_unregister_prefix():
    hub = SensorHub()
    hub.register([Sensor("dev:x:Liquid", "Liquid", "liquid", constant(30.0))])
    hub.poll()
    hub.unregister_prefix("dev:")
    assert hub.sensors == []
    assert hub.values() == {}
