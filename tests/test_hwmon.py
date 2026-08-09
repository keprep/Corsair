import pytest

from corsair_control.core import hwmon
from corsair_control.core.device import ManagedDevice
from corsair_control.core.hwmon import HwmonBackend, discover_hwmon_backends


@pytest.fixture
def chip(tmp_path):
    """A fake hwmon chip with two PWM outputs and tachometers."""
    directory = tmp_path / "hwmon3"
    directory.mkdir()
    (directory / "name").write_text("nct6798\n")
    for index in (1, 2):
        (directory / f"pwm{index}").write_text("128\n")
        (directory / f"pwm{index}_enable").write_text("2\n")
        (directory / f"fan{index}_input").write_text(f"{index * 500}\n")
    (directory / "temp1_input").write_text("42000\n")
    return directory


def test_channels_are_discovered(chip):
    backend = HwmonBackend(chip, "nct6798")
    assert backend.channels == ["fan1", "fan2"]


def test_status_reports_speed_and_duty(chip):
    backend = HwmonBackend(chip, "nct6798")
    status = dict((key, value) for key, value, _unit in backend.get_status())
    assert status["Fan 1 speed"] == 500
    assert status["Fan 2 speed"] == 1000
    assert status["Fan 1 duty"] == pytest.approx(50.2, abs=0.2)


def test_setting_a_duty_switches_to_manual(chip):
    backend = HwmonBackend(chip, "nct6798")
    backend.initialize()
    backend.set_fixed_speed("fan1", 100)
    assert (chip / "pwm1").read_text() == "255"
    assert (chip / "pwm1_enable").read_text() == "1"


def test_fan_keyword_sets_every_channel(chip):
    backend = HwmonBackend(chip, "nct6798")
    backend.set_fixed_speed("fan", 0)
    assert (chip / "pwm1").read_text() == "0"
    assert (chip / "pwm2").read_text() == "0"


def test_unknown_channel_raises(chip):
    backend = HwmonBackend(chip, "nct6798")
    with pytest.raises(ValueError):
        backend.set_fixed_speed("fan9", 50)


def test_restore_puts_back_the_original_mode(chip):
    backend = HwmonBackend(chip, "nct6798")
    backend.initialize()
    backend.set_fixed_speed("fan1", 40)
    assert (chip / "pwm1_enable").read_text() == "1"
    backend.restore_automatic()
    assert (chip / "pwm1_enable").read_text() == "2"


def test_stable_key_survives_renumbering(chip):
    backend = HwmonBackend(chip, "nct6798")
    assert backend.stable_key.startswith("hwmon:nct6798:")


def test_managed_device_wraps_the_backend(chip):
    device = ManagedDevice(HwmonBackend(chip, "nct6798"))
    device.connect()
    device.initialize()
    device.probe()
    assert device.is_hwmon
    assert {c.channel_id for c in device.channels} == {"fan1", "fan2"}
    assert device.pump_modes == []
    assert not device.supports_lighting
    # The chip's temperature belongs to the sensor hub, not the device status.
    assert device.last_status.temperatures == {}


def test_discovery_skips_chips_handled_over_usb(tmp_path, monkeypatch):
    root = tmp_path / "hwmon"
    root.mkdir()
    for name, chip_name in (("hwmon0", "corsair-cpro"), ("hwmon1", "nct6798")):
        directory = root / name
        directory.mkdir()
        (directory / "name").write_text(chip_name)
        (directory / "pwm1").write_text("100")
    monkeypatch.setattr(hwmon, "HWMON_ROOT", root)

    found = discover_hwmon_backends()
    assert [b.chip for b in found] == ["nct6798"]
    assert [b.chip for b in discover_hwmon_backends(include_all=True)] == [
        "corsair-cpro",
        "nct6798",
    ]


def test_discovery_ignores_chips_without_pwm(tmp_path, monkeypatch):
    root = tmp_path / "hwmon"
    root.mkdir()
    directory = root / "hwmon0"
    directory.mkdir()
    (directory / "name").write_text("k10temp")
    (directory / "temp1_input").write_text("40000")
    monkeypatch.setattr(hwmon, "HWMON_ROOT", root)
    assert discover_hwmon_backends() == []


def test_missing_sysfs_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(hwmon, "HWMON_ROOT", tmp_path / "nope")
    assert discover_hwmon_backends() == []


def test_unclaimed_corsair_reports_ids(monkeypatch):
    """A device no driver claims must be named, not silently absent."""
    from corsair_control.core import manager

    fake_entries = [
        {"product_id": 0x0C1C, "product_string": "Commander CORE"},
        {"product_id": 0x0C40, "product_string": "H150i ELITE CAPELLIX XT"},
    ]

    class FakeHid:
        @staticmethod
        def enumerate(vendor, product):
            return fake_entries

    monkeypatch.setitem(__import__("sys").modules, "hid", FakeHid)

    class Claimed:
        def __init__(self, pid):
            self._dev = type("B", (), {"product_id": pid})()

    unclaimed = manager.unclaimed_corsair([Claimed(0x0C1C)])
    assert unclaimed == [(0x1B1C, 0x0C40, "H150i ELITE CAPELLIX XT")]


def test_unclaimed_corsair_survives_a_broken_hid_backend(monkeypatch):
    from corsair_control.core import manager

    class BoomHid:
        @staticmethod
        def enumerate(vendor, product):
            raise OSError("no permission")

    monkeypatch.setitem(__import__("sys").modules, "hid", BoomHid)
    assert manager.unclaimed_corsair([]) == []
