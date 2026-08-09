"""What survives on a platform that is not Linux.

The project targets Linux, but the GUI and the demo mode have no business
crashing elsewhere. These tests simulate a non-POSIX host by removing
``os.geteuid`` and pointing the Linux-only sysfs roots at nothing.
"""

import os

import pytest

from corsair_control.core import automation, config, hwmon, sensors
from corsair_control.core.config import Settings
from corsair_control.core.engine import ControlEngine
from corsair_control.core.profile import ProfileStore


@pytest.fixture
def windows_like(tmp_path, monkeypatch):
    """No geteuid, no /sys, no /proc - the shape of a Windows host."""
    monkeypatch.delattr(os, "geteuid", raising=False)
    monkeypatch.setattr(sensors, "HWMON_ROOT", tmp_path / "no-sysfs")
    monkeypatch.setattr(hwmon, "HWMON_ROOT", tmp_path / "no-sysfs")
    monkeypatch.setattr(automation, "PROC_ROOT", tmp_path / "no-proc")
    monkeypatch.setattr(automation, "POWER_SUPPLY_ROOT", tmp_path / "no-power")
    monkeypatch.setenv("CORSAIR_CONTROL_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CORSAIR_CONTROL_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_is_root_without_geteuid(windows_like):
    assert config.is_root() is False


def test_config_paths_resolve_without_geteuid(windows_like, monkeypatch):
    monkeypatch.delenv("CORSAIR_CONTROL_CONFIG_DIR")
    monkeypatch.delenv("CORSAIR_CONTROL_STATE_DIR")
    # Must not raise, and must land in the user's home rather than /etc.
    assert config.config_dir().is_absolute()
    assert config.state_dir().is_absolute()
    assert not str(config.config_dir()).startswith("/etc")


def test_sensor_discovery_without_sysfs(windows_like):
    hub = sensors.SensorHub()
    hub.discover()
    assert hub.sensors == [] or all(s.category != "board" for s in hub.sensors)


def test_hwmon_discovery_without_sysfs(windows_like):
    assert hwmon.discover_hwmon_backends() == []


def test_process_watcher_without_proc(windows_like):
    assert automation.ProcessWatcher().names() == frozenset()


def test_power_state_without_sysfs(windows_like):
    assert automation.power_state() is None


def test_automation_rules_still_evaluate(windows_like):
    engine = automation.AutomationEngine(
        rules=[automation.Rule(name="hot", profile="Performance", temperature_above=50)],
        default_profile="Balanced",
    )
    assert engine.choose(engine.context(hottest=80)) == "Performance"


def test_demo_mode_runs_end_to_end(windows_like):
    """The whole engine has to come up and control the simulated hardware."""
    settings = Settings(poll_interval=0.5)
    store = ProfileStore(windows_like / "config" / "profiles.json")
    store.load()
    engine = ControlEngine(settings, store, demo=True)
    try:
        assert engine.discover() == []
        snapshot = engine.tick()
        assert len(snapshot.devices) == 2
        assert snapshot.devices[0].channels[0].rpm is not None
        # Profiles must be writable in the per-user location.
        store.save()
        assert (windows_like / "config" / "profiles.json").exists()
    finally:
        engine.stop()


def test_window_builds_without_posix(windows_like):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    from corsair_control.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    assert app is not None

    settings = Settings(poll_interval=0.5, close_to_tray=False)
    store = ProfileStore(windows_like / "config" / "profiles.json")
    store.load()
    engine = ControlEngine(settings, store, demo=True)
    engine.discover()
    window = MainWindow(engine, settings, store, demo=True)
    try:
        window._on_snapshot(engine.tick())
        assert window.stack.count() == 6
    finally:
        window.close()
        engine.stop()
