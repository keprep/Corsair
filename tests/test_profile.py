import json

from corsair_control.core.device import Channel
from corsair_control.core.automation import Rule
from corsair_control.core.calibration import CalibrationPoint, ChannelCalibration
from corsair_control.core.profile import (
    MODE_CURVE,
    ChannelConfig,
    Profile,
    ProfileStore,
    SensorSource,
)


def test_channel_config_clamps_to_limits():
    config = ChannelConfig(min_duty=30, max_duty=80)
    assert config.clamp(10) == 30
    assert config.clamp(95) == 80
    assert config.clamp(50) == 50


def test_zero_rpm_guard():
    config = ChannelConfig(min_duty=0, allow_zero_rpm=False)
    assert config.clamp(0) == 1
    config.allow_zero_rpm = True
    assert config.clamp(0) == 0


def test_store_round_trip(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    store.add("Night", copy_from="Silent")
    store.activate("Night")
    store.save()

    reloaded = ProfileStore(tmp_path / "profiles.json")
    reloaded.load()
    assert reloaded.active_name == "Night"
    assert set(reloaded.names()) >= {"Silent", "Balanced", "Performance", "Night"}


def test_store_recovers_from_broken_file(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{not json", encoding="utf-8")
    store = ProfileStore(path)
    store.load()
    assert store.names()


def test_seed_defaults_fills_every_profile(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    store.add("Custom")
    channels = [
        Channel("fan1", "Fan 1", "fan"),
        Channel("pump", "Pump", "pump"),
    ]
    assert store.seed_defaults("dev0", channels, "demo:cpu") is True

    for name in store.names():
        device = store.profiles[name].devices["dev0"]
        assert set(device.channels) == {"fan1", "pump"}
        assert device.channels["fan1"].mode == MODE_CURVE
        assert device.channels["pump"].min_duty >= 50

    # A second call must not report changes.
    assert store.seed_defaults("dev0", channels, "demo:cpu") is False


def test_profile_json_is_human_editable(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    store.save()
    data = json.loads((tmp_path / "profiles.json").read_text())
    assert data["version"] == 2
    assert "profiles" in data
    assert "automation" in data and "alarms" in data


def test_profile_copy_is_deep():
    profile = Profile(name="A")
    profile.device("dev").channel("fan1").min_duty = 42
    clone = profile.copy_as("B")
    clone.devices["dev"].channels["fan1"].min_duty = 10
    assert profile.devices["dev"].channels["fan1"].min_duty == 42


def test_multi_sensor_max_is_the_default():
    config = ChannelConfig()
    config.set_sensor_ids(["cpu", "gpu"])
    assert config.temperature({"cpu": 50.0, "gpu": 70.0}) == 70.0


def test_multi_sensor_average():
    config = ChannelConfig(source_mode="average")
    config.set_sensor_ids(["cpu", "gpu"])
    assert config.temperature({"cpu": 50.0, "gpu": 70.0}) == 60.0


def test_multi_sensor_weighted():
    config = ChannelConfig(source_mode="weighted")
    config.sources = [SensorSource("cpu", 3.0), SensorSource("gpu", 1.0)]
    assert config.temperature({"cpu": 40.0, "gpu": 80.0}) == 50.0


def test_missing_sensor_is_skipped():
    config = ChannelConfig()
    config.set_sensor_ids(["cpu", "gone"])
    assert config.temperature({"cpu": 55.0}) == 55.0
    assert config.temperature({}) is None


def test_weights_survive_a_reselection():
    config = ChannelConfig()
    config.sources = [SensorSource("cpu", 4.0)]
    config.set_sensor_ids(["cpu", "gpu"])
    assert config.sources[0].weight == 4.0
    assert config.sources[1].weight == 1.0


def test_sensor_id_shim_reads_and_writes_the_source_list():
    config = ChannelConfig()
    config.sensor_id = "cpu"
    assert config.sources[0].sensor_id == "cpu"
    assert config.sensor_id == "cpu"


def test_old_profiles_migrate_to_the_source_list():
    config = ChannelConfig.from_dict({"sensor_id": "hwmon:k10temp:hwmon2:temp1"})
    assert config.sensor_ids == ["hwmon:k10temp:hwmon2:temp1"]


def test_channel_config_round_trip_keeps_sources_and_calibration():
    config = ChannelConfig(source_mode="average")
    config.sources = [SensorSource("cpu", 2.0), SensorSource("gpu", 0.5)]
    config.calibration = ChannelCalibration(
        points=[CalibrationPoint(0, 0), CalibrationPoint(100, 1800)], stall_duty=20
    )
    restored = ChannelConfig.from_dict(config.to_dict())
    assert restored.source_mode == "average"
    assert [(s.sensor_id, s.weight) for s in restored.sources] == [("cpu", 2.0), ("gpu", 0.5)]
    assert restored.calibration is not None
    assert restored.calibration.stall_duty == 20


def test_store_persists_automation_and_alarms(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    store.automation_enabled = True
    store.rules = [Rule(name="night", profile="Silent", time_from="22:00", time_to="07:00")]
    store.alarms.pump_minimum_rpm = 555
    store.save()

    reloaded = ProfileStore(tmp_path / "profiles.json")
    reloaded.load()
    assert reloaded.automation_enabled is True
    assert reloaded.rules[0].name == "night"
    assert reloaded.alarms.pump_minimum_rpm == 555
