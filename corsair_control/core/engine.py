"""The control loop.

The engine owns the devices, the sensors and the active profile. It runs in
its own thread, publishes an immutable snapshot after every cycle and is the
only place in the application that writes to hardware.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from corsair_control.core.alarms import Alarm, AlarmMonitor
from corsair_control.core.automation import AutomationEngine
from corsair_control.core.calibration import CalibrationRunner, ChannelCalibration
from corsair_control.core.config import Settings, state_dir
from corsair_control.core.curve import CurveEvaluator
from corsair_control.core.device import Channel, DeviceError, ManagedDevice
from corsair_control.core.manager import discover, release
from corsair_control.core.profile import (
    MODE_FIXED,
    MODE_MANUAL,
    ChannelConfig,
    ProfileStore,
)
from corsair_control.core.recorder import Recorder
from corsair_control.core.sensors import Sensor, SensorHub

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelSnapshot:
    device_key: str
    channel_id: str
    label: str
    kind: str
    controllable: bool
    mode: str
    rpm: float | None
    duty: float | None
    target_duty: float | None
    sensor_id: str | None
    sensor_label: str
    sensor_value: float | None
    error: str | None = None


@dataclass(frozen=True)
class DeviceSnapshot:
    key: str
    name: str
    description: str
    driver: str
    bus_info: str
    temperatures: dict[str, float]
    channels: tuple[ChannelSnapshot, ...]
    error: str | None = None


@dataclass(frozen=True)
class Snapshot:
    timestamp: float = 0.0
    profile: str = ""
    devices: tuple[DeviceSnapshot, ...] = ()
    sensors: dict[str, float] = field(default_factory=dict)
    emergency: bool = False
    messages: tuple[str, ...] = ()
    alarms: tuple[Alarm, ...] = ()
    new_alarms: tuple[Alarm, ...] = ()
    automation_rule: str | None = None
    calibrating: tuple[str, ...] = ()

    def device(self, key: str) -> DeviceSnapshot | None:
        for device in self.devices:
            if device.key == key:
                return device
        return None


class ControlEngine:
    """Polls sensors, evaluates curves and writes duty cycles."""

    def __init__(
        self,
        settings: Settings,
        store: ProfileStore,
        *,
        demo: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.settings = settings
        self.store = store
        self.demo = demo
        self.dry_run = dry_run

        self.sensors = SensorHub(
            history_seconds=settings.history_seconds, poll_interval=settings.poll_interval
        )
        self.devices: list[ManagedDevice] = []
        self.startup_errors: list[str] = []

        self.alarms = AlarmMonitor(settings=store.alarms)
        self.automation = AutomationEngine(
            rules=store.rules, default_profile=store.active_name
        )
        self.automation.enabled = store.automation_enabled
        self.recorder = Recorder(
            state_dir(),
            enabled=settings.record_history,
            retention_days=settings.history_retention_days,
        )

        self._evaluators: dict[tuple[str, str], CurveEvaluator] = {}
        self._offloaded: set[tuple[str, str]] = set()
        self._overrides: dict[tuple[str, str], float] = {}
        self._calibrating: set[tuple[str, str]] = set()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._subscribers: list[Callable[[Snapshot], None]] = []
        self._snapshot = Snapshot()
        self._paused = False
        self._emergency_since: float | None = None

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def discover(self) -> list[str]:
        """Populate devices and sensors. Returns human-readable problems."""
        with self._lock:
            release(self.devices)
            self.devices = []
            self.sensors.unregister_prefix("dev:")

            self.sensors.discover(demo=self.demo)
            result = discover(demo=self.demo, include_hwmon=self.settings.control_mainboard_fans)
            self.devices = result.devices
            self.startup_errors = list(result.errors)

            for device in self.devices:
                self._register_device_sensors(device)

            default_sensor = self.sensors.default_sensor_id()
            dirty = False
            for device in self.devices:
                # Prefer the device's own liquid temperature for an AIO: it is
                # the sensor that actually describes what the pump and radiator
                # fans are working against.
                own = self._preferred_sensor_for(device) or default_sensor
                if self.store.seed_defaults(device.key, device.channels, own):
                    dirty = True
            if dirty:
                try:
                    self.store.save()
                except OSError as exc:
                    log.warning("Cannot store the seeded defaults: %s", exc)

            self._evaluators.clear()
            self._offloaded.clear()
            return self.startup_errors

    def _register_device_sensors(self, device: ManagedDevice) -> None:
        device.refresh()
        sensors: list[Sensor] = []
        for name in device.last_status.temperatures:
            sensor_id = f"dev:{device.key}:{name}"

            def reader(device: ManagedDevice = device, name: str = name) -> float | None:
                return device.last_status.temperatures.get(name)

            lowered = name.lower()
            # Hydro Platinum says "Liquid temperature", Commander Core says
            # "Water temperature" - both are the loop.
            liquid = "liquid" in lowered or "water" in lowered
            category = "liquid" if liquid else "other"
            sensors.append(
                Sensor(
                    sensor_id=sensor_id,
                    label=f"{device.short_name} · {name}",
                    category=category,
                    read=reader,
                    source=device.key,
                )
            )
        self.sensors.register(sensors)

    def _preferred_sensor_for(self, device: ManagedDevice) -> str | None:
        for name in device.last_status.temperatures:
            lowered = name.lower()
            if "liquid" in lowered or "water" in lowered:
                return f"dev:{device.key}:{name}"
        return None

    # ------------------------------------------------------------------
    # thread control
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.recorder.prune()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="corsair-engine", daemon=True)
        self._thread.start()

    def stop(self, *, restore: bool = False) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None
        if restore:
            self.restore_safe_defaults()
        release(self.devices)

    def restore_safe_defaults(self) -> None:
        """Hand the hardware back in a state that cannot cook anything.

        Called on shutdown: without it a device keeps the last duty we wrote,
        which may be a quiet 25 % while the machine is under load. Mainboard
        headers go back to the BIOS controller instead, which is strictly
        better than any fixed value we could pick.
        """
        for device in self.devices:
            if device.is_hwmon:
                device.restore_automatic()
                continue
            for channel in device.channels:
                if not channel.controllable:
                    continue
                try:
                    device.set_duty(channel.channel_id, 100.0 if channel.is_pump else 60.0)
                except DeviceError as exc:
                    log.warning("Cannot reset %s/%s: %s", device.key, channel.channel_id, exc)

    def pause(self, paused: bool = True) -> None:
        self._paused = paused

    @property
    def paused(self) -> bool:
        return self._paused

    def wake(self) -> None:
        """Ask the loop to run a cycle right now (after a UI change)."""
        self._wake.set()

    # ------------------------------------------------------------------
    # subscriptions
    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[Snapshot], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Snapshot], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    # ------------------------------------------------------------------
    # profile handling
    # ------------------------------------------------------------------
    def set_profile(self, name: str, *, manual: bool = True) -> None:
        with self._lock:
            self.store.activate(name)
            self.settings.active_profile = name
            if manual:
                # Remember the choice so the automation does not immediately
                # switch back to the same profile it last picked.
                self.automation.note_manual_change(name)
                self.automation.default_profile = name
            self._evaluators.clear()
            self._offloaded.clear()
            self._overrides.clear()
        self.wake()

    def config_changed(self, device_key: str | None = None, channel_id: str | None = None) -> None:
        """Drop cached state so an edited curve takes effect immediately."""
        with self._lock:
            if device_key and channel_id:
                self._evaluators.pop((device_key, channel_id), None)
                self._offloaded.discard((device_key, channel_id))
                self._overrides.pop((device_key, channel_id), None)
            else:
                self._evaluators.clear()
                self._offloaded.clear()
                self._overrides.clear()
                # A full reload replaces the store's rule list and alarm
                # settings objects, so the engine has to be re-pointed at them.
                self.automation.rules = self.store.rules
                self.automation.enabled = self.store.automation_enabled
                self.alarms.settings = self.store.alarms
        self.wake()

    def set_override(self, device_key: str, channel_id: str, duty: float | None) -> None:
        """Temporarily force a duty (used while a slider is being dragged)."""
        with self._lock:
            if duty is None:
                self._overrides.pop((device_key, channel_id), None)
            else:
                self._overrides[(device_key, channel_id)] = float(duty)
        self.wake()

    # ------------------------------------------------------------------
    # loop
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick()
            except Exception:  # pragma: no cover - the loop must never die
                log.exception("Control cycle failed")
            elapsed = time.monotonic() - started
            delay = max(0.2, self.settings.poll_interval - elapsed)
            if self._wake.wait(delay):
                self._wake.clear()

    def tick(self) -> Snapshot:
        """One full cycle: read hardware, evaluate curves, write duties."""
        with self._lock:
            messages: list[str] = []
            for device in self.devices:
                device.refresh()

            sensor_values = self.sensors.poll()
            emergency = self._check_emergency(sensor_values)
            self._run_automation()

            device_snapshots: list[DeviceSnapshot] = []
            profile = self.store.active

            for device in self.devices:
                status = device.last_status
                channel_snapshots: list[ChannelSnapshot] = []
                device_config = profile.device(device.key)

                for channel in device.channels:
                    config = device_config.channel(channel.channel_id)
                    target, sensor_value, error = self._evaluate(
                        device, channel, config, emergency, sensor_values
                    )
                    channel_snapshots.append(
                        ChannelSnapshot(
                            device_key=device.key,
                            channel_id=channel.channel_id,
                            label=channel.label,
                            kind=channel.kind,
                            controllable=channel.controllable,
                            mode=config.mode,
                            rpm=status.speeds.get(channel.channel_id),
                            duty=status.duties.get(channel.channel_id)
                            or device.applied_duty(channel.channel_id),
                            target_duty=target,
                            sensor_id=config.sensor_id,
                            sensor_label=self.source_label(config),
                            sensor_value=sensor_value,
                            error=error,
                        )
                    )
                    if error:
                        messages.append(f"{device.short_name} · {channel.label}: {error}")

                device_snapshots.append(
                    DeviceSnapshot(
                        key=device.key,
                        name=device.short_name,
                        description=device.description,
                        driver=device.driver,
                        bus_info=device.bus_info,
                        temperatures=dict(status.temperatures),
                        channels=tuple(channel_snapshots),
                        error=status.error,
                    )
                )
                if status.error:
                    messages.append(f"{device.short_name}: {status.error}")

            snapshot = Snapshot(
                timestamp=time.time(),
                profile=self.store.active_name,
                devices=tuple(device_snapshots),
                sensors=sensor_values,
                emergency=emergency,
                messages=tuple(messages),
                automation_rule=(
                    self.automation.active_rule.name if self.automation.active_rule else None
                ),
                calibrating=tuple(f"{d}/{c}" for d, c in self._calibrating),
            )

            raised, _cleared = self.alarms.evaluate(snapshot)
            snapshot = replace(
                snapshot, alarms=tuple(self.alarms.active), new_alarms=tuple(raised)
            )
            self.recorder.record(snapshot, self._sensor_labels())

        self._snapshot = snapshot
        for callback in list(self._subscribers):
            try:
                callback(snapshot)
            except Exception:  # pragma: no cover - a bad listener is not fatal
                log.exception("Snapshot subscriber failed")
        return snapshot

    def _check_emergency(self, sensor_values: dict[str, float]) -> bool:
        limit = self.settings.emergency_temperature
        hottest = self.sensors.hottest()
        if hottest is not None and hottest >= limit:
            if self._emergency_since is None:
                self._emergency_since = time.monotonic()
                log.warning("Emergency: %.1f °C reached the limit of %.1f °C", hottest, limit)
            return True
        # Leave the emergency state with a margin so it cannot oscillate.
        if self._emergency_since is not None:
            if hottest is not None and hottest > limit - 5:
                return True
            self._emergency_since = None
            log.info("Emergency cleared")
        return False

    def source_label(self, config: ChannelConfig) -> str:
        """Human-readable summary of a channel's temperature sources."""
        labels = []
        for source in config.sources:
            sensor = self.sensors.get(source.sensor_id)
            labels.append(sensor.label if sensor else source.sensor_id)
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        short = [label.split(" · ")[0] for label in labels]
        return f"{' + '.join(short)} ({config.source_mode})"

    def _evaluate(
        self,
        device: ManagedDevice,
        channel: Channel,
        config: ChannelConfig,
        emergency: bool,
        sensor_values: dict[str, float],
    ) -> tuple[float | None, float | None, str | None]:
        key = (device.key, channel.channel_id)
        sensor_value = config.temperature(sensor_values)

        if not channel.controllable or self._paused or key in self._calibrating:
            return None, sensor_value, None

        if emergency:
            return self._write(device, channel, 100.0, key, force=True), sensor_value, None

        override = self._overrides.get(key)
        if override is not None:
            return self._write(device, channel, config.clamp(override), key), sensor_value, None

        if config.mode == MODE_MANUAL:
            return None, sensor_value, None

        if config.mode == MODE_FIXED:
            duty = config.clamp(config.fixed_duty)
            return self._write(device, channel, duty, key), sensor_value, None

        # curve mode
        if config.offload_to_hardware and device.supports_hardware_curves:
            if key not in self._offloaded:
                try:
                    device.set_speed_profile(
                        channel.channel_id, [p.as_tuple() for p in config.curve.points]
                    )
                    self._offloaded.add(key)
                except DeviceError as exc:
                    return None, sensor_value, str(exc)
            return None, sensor_value, None

        if sensor_value is None:
            fallback = self.sensors.hottest()
            if fallback is None:
                return None, sensor_value, "no temperature source available"
            sensor_value = fallback

        evaluator = self._evaluators.get(key)
        if evaluator is None:
            evaluator = CurveEvaluator(min_step_seconds=max(1.0, self.settings.poll_interval))
            self._evaluators[key] = evaluator

        raw = evaluator.feed(sensor_value, config.curve, time.monotonic())
        target = config.clamp(evaluator.committed_duty or 0.0)
        if raw is None:
            return target, sensor_value, None
        return self._write(device, channel, config.clamp(raw), key), sensor_value, None

    def _sensor_labels(self) -> dict[str, str]:
        return {sensor.sensor_id: sensor.label for sensor in self.sensors.sensors}

    # ------------------------------------------------------------------
    # automation
    # ------------------------------------------------------------------
    def _run_automation(self) -> None:
        if not self.automation.enabled or not self.automation.rules:
            return
        context = self.automation.context(hottest=self.sensors.hottest())
        target = self.automation.choose(context)
        if target and target != self.store.active_name:
            log.info("Automation switches to profile '%s'", target)
            self.set_profile(target, manual=False)

    # ------------------------------------------------------------------
    # calibration
    # ------------------------------------------------------------------
    @property
    def calibrating(self) -> set[tuple[str, str]]:
        return set(self._calibrating)

    def calibrate(
        self,
        device_key: str,
        channel_id: str,
        *,
        progress=None,
        cancel=None,
        settle_seconds: float = 3.5,
        samples: int = 3,
        sample_interval: float = 0.4,
    ) -> ChannelCalibration:
        """Sweep one channel and store the result in the active profile.

        The channel is excluded from the control loop while this runs, and the
        loop keeps servicing everything else - a calibration takes a minute or
        two and the rest of the machine still needs cooling.
        """
        device = self.device_by_key(device_key)
        if device is None:
            raise DeviceError("device is gone")
        channel = next((c for c in device.channels if c.channel_id == channel_id), None)
        if channel is None or not channel.controllable:
            raise DeviceError("this channel cannot be controlled")

        key = (device_key, channel_id)
        previous = device.applied_duty(channel_id)
        with self._lock:
            self._calibrating.add(key)

        def read_rpm() -> float | None:
            device.refresh()
            return device.last_status.speeds.get(channel_id)

        runner = CalibrationRunner(
            set_duty=lambda duty: device.set_duty(channel_id, duty),
            read_rpm=read_rpm,
            settle_seconds=settle_seconds,
            samples=samples,
            sample_interval=sample_interval,
        )
        try:
            result = runner.run(progress=progress, cancel=cancel)
        finally:
            with self._lock:
                self._calibrating.discard(key)
                self._evaluators.pop(key, None)
            if previous is not None:
                try:
                    device.set_duty(channel_id, previous)
                except DeviceError:  # pragma: no cover - best effort
                    pass

        config = self.store.active.device(device_key).channel(channel_id)
        config.calibration = result
        suggestion = result.safe_minimum()
        if suggestion is not None and config.min_duty < suggestion and not config.allow_zero_rpm:
            config.min_duty = round(suggestion, 1)
        try:
            self.store.save()
        except OSError as exc:
            log.warning("Cannot store the calibration: %s", exc)
        self.wake()
        return result

    def _write(
        self,
        device: ManagedDevice,
        channel: Channel,
        duty: float,
        key: tuple[str, str],
        *,
        force: bool = False,
    ) -> float | None:
        previous = device.applied_duty(channel.channel_id)
        if not force and previous is not None and abs(previous - duty) < 0.5:
            return duty
        if self.dry_run:
            log.info("[dry-run] %s/%s -> %.0f %%", device.short_name, channel.channel_id, duty)
            return duty
        try:
            device.set_duty(channel.channel_id, duty)
        except DeviceError as exc:
            log.warning("Cannot set %s/%s: %s", device.key, channel.channel_id, exc)
            return None
        return duty

    # ------------------------------------------------------------------
    # one-off actions used by the UI
    # ------------------------------------------------------------------
    def device_by_key(self, key: str) -> ManagedDevice | None:
        for device in self.devices:
            if device.key == key:
                return device
        return None

    def apply_lighting(
        self,
        device_key: str,
        channel_id: str,
        mode: str,
        colors,
        *,
        speed: str | None = None,
        direction: str | None = None,
    ) -> str | None:
        device = self.device_by_key(device_key)
        if device is None:
            return "device is gone"
        try:
            device.set_lighting(channel_id, mode, colors, speed=speed, direction=direction)
        except DeviceError as exc:
            return str(exc)
        return None

    def apply_screen(self, device_key: str, channel_id: str, mode: str, value: str) -> str | None:
        device = self.device_by_key(device_key)
        if device is None:
            return "device is gone"
        try:
            device.set_screen(channel_id, mode, value)
        except DeviceError as exc:
            return str(exc)
        return None

    def apply_pump_mode(self, device_key: str, mode: str) -> str | None:
        device = self.device_by_key(device_key)
        if device is None:
            return "device is gone"
        try:
            device.set_pump_mode(mode)
        except DeviceError as exc:
            return str(exc)
        return None
