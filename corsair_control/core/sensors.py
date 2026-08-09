"""Temperature sources: Linux hwmon, NVIDIA via ``nvidia-smi``, and virtual
aggregates such as "hottest of all CPU sensors"."""

from __future__ import annotations

import logging
import random
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

HWMON_ROOT = Path("/sys/class/hwmon")

CPU_CHIPS = {
    "coretemp",
    "k10temp",
    "k8temp",
    "zenpower",
    "cpu_thermal",
    "cpu-thermal",
    "atk0110",
}
GPU_CHIPS = {"amdgpu", "radeon", "nouveau", "nvidia", "i915", "xe"}
STORAGE_CHIPS = {"nvme", "drivetemp"}

#: hwmon chips that report a whole zoo of board sensors. They are still
#: offered, just sorted below the interesting ones.
BOARD_CHIPS = {"nct6775", "nct6798", "it87", "it8686", "asus_wmi_sensors"}


@dataclass
class SensorReading:
    sensor_id: str
    value: float
    timestamp: float


@dataclass
class Sensor:
    """A single readable temperature in degrees Celsius."""

    sensor_id: str
    label: str
    category: str  # cpu | gpu | liquid | storage | board | other | virtual
    read: Callable[[], float | None]
    source: str = ""

    def __hash__(self) -> int:  # allows use as dict key in the UI
        return hash(self.sensor_id)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _categorise(chip: str) -> str:
    chip = chip.lower()
    if chip in CPU_CHIPS:
        return "cpu"
    if chip in GPU_CHIPS:
        return "gpu"
    if chip in STORAGE_CHIPS:
        return "storage"
    if chip in BOARD_CHIPS:
        return "board"
    return "other"


def _pretty_chip(chip: str) -> str:
    return {
        "k10temp": "AMD CPU",
        "zenpower": "AMD CPU",
        "coretemp": "Intel CPU",
        "amdgpu": "AMD GPU",
        "nouveau": "NVIDIA GPU",
        "nvme": "NVMe",
        "drivetemp": "Drive",
    }.get(chip.lower(), chip)


def discover_hwmon() -> list[Sensor]:
    """Enumerate every ``tempN_input`` exposed under /sys/class/hwmon."""
    sensors: list[Sensor] = []
    if not HWMON_ROOT.exists():
        return sensors

    for hwmon in sorted(HWMON_ROOT.glob("hwmon*")):
        chip = _read_text(hwmon / "name") or hwmon.name
        for input_path in sorted(hwmon.glob("temp*_input")):
            match = re.match(r"temp(\d+)_input", input_path.name)
            if not match:
                continue
            index = match.group(1)
            label = _read_text(hwmon / f"temp{index}_label") or f"temp{index}"
            sensor_id = f"hwmon:{chip}:{hwmon.name}:temp{index}"
            path = input_path

            def reader(path: Path = path) -> float | None:
                raw = _read_int(path)
                return None if raw is None else raw / 1000.0

            sensors.append(
                Sensor(
                    sensor_id=sensor_id,
                    label=f"{_pretty_chip(chip)} · {label}",
                    category=_categorise(chip),
                    read=reader,
                    source=str(path),
                )
            )
    return sensors


class NvidiaSmiSensor:
    """Reads GPU temperature from ``nvidia-smi``.

    The proprietary driver does not always register an hwmon node, so this
    fallback keeps NVIDIA users from losing their GPU as a curve source. The
    result is cached because spawning the binary is comparatively expensive.
    """

    def __init__(self, index: int, cache_seconds: float = 2.0) -> None:
        self.index = index
        self.cache_seconds = cache_seconds
        self._value: float | None = None
        self._read_at = 0.0

    def __call__(self) -> float | None:
        now = time.monotonic()
        if self._value is not None and now - self._read_at < self.cache_seconds:
            return self._value
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    f"--id={self.index}",
                    "--query-gpu=temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("nvidia-smi failed: %s", exc)
            return None
        line = out.stdout.strip().splitlines()
        if not line:
            return None
        try:
            self._value = float(line[0].strip())
        except ValueError:
            return None
        self._read_at = now
        return self._value


def discover_nvidia() -> list[Sensor]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    sensors = []
    for index, name in enumerate(out.stdout.strip().splitlines()):
        sensors.append(
            Sensor(
                sensor_id=f"nvidia:{index}",
                label=f"NVIDIA GPU {index} · {name.strip()}",
                category="gpu",
                read=NvidiaSmiSensor(index),
                source="nvidia-smi",
            )
        )
    return sensors


def demo_sensors() -> list[Sensor]:
    """Synthetic sensors used by ``--demo`` and by the test-suite."""
    state = {"cpu": 42.0, "gpu": 38.0, "t": 0.0}

    def cpu() -> float:
        state["t"] += 0.35
        import math

        base = 45 + 22 * math.sin(state["t"] / 9.0) ** 2
        state["cpu"] += (base - state["cpu"]) * 0.25 + random.uniform(-0.6, 0.6)
        return round(state["cpu"], 1)

    def gpu() -> float:
        import math

        base = 40 + 25 * math.sin(state["t"] / 14.0 + 1.2) ** 2
        state["gpu"] += (base - state["gpu"]) * 0.2 + random.uniform(-0.5, 0.5)
        return round(state["gpu"], 1)

    return [
        Sensor("demo:cpu", "CPU · Tctl (Demo)", "cpu", cpu, "demo"),
        Sensor("demo:gpu", "GPU · Edge (Demo)", "gpu", gpu, "demo"),
    ]


class SensorHub:
    """Owns the sensor list, the latest values and a bounded history."""

    def __init__(self, history_seconds: int = 300, poll_interval: float = 1.5) -> None:
        self._sensors: dict[str, Sensor] = {}
        self._values: dict[str, float] = {}
        depth = max(30, int(history_seconds / max(poll_interval, 0.25)))
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._depth = depth

    # ------------------------------------------------------------------
    def register(self, sensors: Iterable[Sensor]) -> None:
        for sensor in sensors:
            self._sensors[sensor.sensor_id] = sensor
            self._history.setdefault(sensor.sensor_id, deque(maxlen=self._depth))

    def unregister_prefix(self, prefix: str) -> None:
        for key in [k for k in self._sensors if k.startswith(prefix)]:
            self._sensors.pop(key, None)
            self._history.pop(key, None)
            self._values.pop(key, None)

    def discover(self, *, demo: bool = False) -> None:
        if demo:
            self.register(demo_sensors())
            return
        self.register(discover_hwmon())
        self.register(discover_nvidia())
        self._register_virtual()

    def _register_virtual(self) -> None:
        """Add ``max(category)`` aggregates for the categories we found.

        A curve bound to "hottest CPU core" behaves far better than one bound
        to an arbitrary single core.
        """
        for category, label in (("cpu", "CPU"), ("gpu", "GPU"), ("storage", "Storage")):
            members = [s.sensor_id for s in self._sensors.values() if s.category == category]
            if len(members) < 2:
                continue
            sid = f"virtual:max:{category}"

            def reader(members: list[str] = members) -> float | None:
                values = [self._values[m] for m in members if m in self._values]
                return max(values) if values else None

            self.register(
                [
                    Sensor(
                        sensor_id=sid,
                        label=f"{label} · hottest sensor",
                        category="virtual",
                        read=reader,
                        source="aggregate",
                    )
                ]
            )

    # ------------------------------------------------------------------
    @property
    def sensors(self) -> list[Sensor]:
        order = {"cpu": 0, "gpu": 1, "liquid": 2, "virtual": 3, "storage": 4, "board": 5}
        return sorted(
            self._sensors.values(),
            key=lambda s: (order.get(s.category, 9), s.label.lower()),
        )

    def get(self, sensor_id: str) -> Sensor | None:
        return self._sensors.get(sensor_id)

    def value(self, sensor_id: str) -> float | None:
        return self._values.get(sensor_id)

    def values(self) -> dict[str, float]:
        return dict(self._values)

    def history(self, sensor_id: str) -> list[tuple[float, float]]:
        return list(self._history.get(sensor_id, ()))

    def poll(self) -> dict[str, float]:
        """Read every sensor once and append to the history."""
        now = time.monotonic()
        # Virtual sensors read from ``self._values``, so they must be polled
        # after the physical ones.
        ordered = sorted(self._sensors.values(), key=lambda s: s.category == "virtual")
        for sensor in ordered:
            try:
                value = sensor.read()
            except Exception as exc:  # a dying sensor must not kill the loop
                log.debug("Sensor %s failed: %s", sensor.sensor_id, exc)
                value = None
            if value is None:
                continue
            self._values[sensor.sensor_id] = value
            self._history.setdefault(sensor.sensor_id, deque(maxlen=self._depth)).append(
                (now, value)
            )
        return dict(self._values)

    def hottest(self, categories: tuple[str, ...] = ("cpu", "gpu", "liquid")) -> float | None:
        candidates = [
            self._values[s.sensor_id]
            for s in self._sensors.values()
            if s.category in categories and s.sensor_id in self._values
        ]
        return max(candidates) if candidates else None

    def default_sensor_id(self, prefer: str = "cpu") -> str | None:
        for category in (prefer, "cpu", "liquid", "gpu"):
            virtual = f"virtual:max:{category}"
            if virtual in self._sensors:
                return virtual
            for sensor in self.sensors:
                if sensor.category == category:
                    return sensor.sensor_id
        return next(iter(self._sensors), None)
