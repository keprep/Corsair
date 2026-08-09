import json

from corsair_control.core.device import Channel
from corsair_control.core.profile import (
    MODE_CURVE,
    ChannelConfig,
    Profile,
    ProfileStore,
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
    assert data["version"] == 1
    assert "profiles" in data


def test_profile_copy_is_deep():
    profile = Profile(name="A")
    profile.device("dev").channel("fan1").min_duty = 42
    clone = profile.copy_as("B")
    clone.devices["dev"].channels["fan1"].min_duty = 10
    assert profile.devices["dev"].channels["fan1"].min_duty == 42
