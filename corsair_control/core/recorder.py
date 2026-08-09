"""Recording and exporting the measurement history.

Two separate things live here:

* :class:`Recorder` appends every cycle to a daily CSV, so the question "why
  did it get loud last night" has an answer the next morning;
* :func:`export_history` dumps what is currently in memory to a file the user
  picks, for a quick look in a spreadsheet.
"""

from __future__ import annotations

import csv
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corsair_control.core.engine import Snapshot
    from corsair_control.core.sensors import SensorHub

log = logging.getLogger(__name__)

HEADER = ["timestamp", "iso", "kind", "id", "label", "value", "unit"]
FILE_PREFIX = "history-"


class Recorder:
    """Appends snapshots to ``history-YYYY-MM-DD.csv``."""

    def __init__(
        self,
        directory: Path,
        *,
        enabled: bool = False,
        retention_days: int = 14,
        interval: float = 5.0,
    ) -> None:
        self.directory = directory
        self.enabled = enabled
        self.retention_days = retention_days
        self.interval = interval
        self._last_write = 0.0
        self._warned = False

    # ------------------------------------------------------------------
    def path_for(self, when: datetime | None = None) -> Path:
        when = when or datetime.now()
        return self.directory / f"{FILE_PREFIX}{when:%Y-%m-%d}.csv"

    def files(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob(f"{FILE_PREFIX}*.csv"))

    def prune(self) -> int:
        """Delete files older than the retention window. Returns the count."""
        if self.retention_days <= 0:
            return 0
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        removed = 0
        for path in self.files():
            stamp = path.stem[len(FILE_PREFIX) :]
            try:
                when = datetime.strptime(stamp, "%Y-%m-%d")
            except ValueError:
                continue
            if when < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError as exc:  # pragma: no cover - permissions
                    log.debug("Cannot delete %s: %s", path, exc)
        return removed

    # ------------------------------------------------------------------
    def record(self, snapshot: "Snapshot", labels: dict[str, str] | None = None) -> bool:
        """Append one snapshot. Returns whether anything was written."""
        if not self.enabled:
            return False
        now = time.monotonic()
        if now - self._last_write < self.interval:
            return False
        self._last_write = now

        rows = list(_rows_for(snapshot, labels or {}))
        if not rows:
            return False

        path = self.path_for()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if new:
                    writer.writerow(HEADER)
                writer.writerows(rows)
        except OSError as exc:
            if not self._warned:
                log.warning("Cannot write the history to %s: %s", path, exc)
                self._warned = True
            return False
        self._warned = False
        return True


def _rows_for(snapshot: "Snapshot", labels: dict[str, str]) -> Iterable[list]:
    stamp = snapshot.timestamp or time.time()
    iso = datetime.fromtimestamp(stamp).isoformat(timespec="seconds")
    for sensor_id, value in sorted(snapshot.sensors.items()):
        yield [
            f"{stamp:.0f}",
            iso,
            "sensor",
            sensor_id,
            labels.get(sensor_id, sensor_id),
            f"{value:.2f}",
            "C",
        ]
    for device in snapshot.devices:
        for channel in device.channels:
            base = f"{device.key}/{channel.channel_id}"
            label = f"{device.name} · {channel.label}"
            if channel.rpm is not None:
                yield [f"{stamp:.0f}", iso, "speed", base, label, f"{channel.rpm:.0f}", "rpm"]
            if channel.duty is not None:
                yield [f"{stamp:.0f}", iso, "duty", base, label, f"{channel.duty:.1f}", "%"]


def export_history(hub: "SensorHub", path: Path) -> int:
    """Write the in-memory sensor history to ``path``. Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    wall_now = time.time()
    monotonic_now = time.monotonic()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["iso", "sensor_id", "label", "value", "unit"])
        for sensor in hub.sensors:
            for timestamp, value in hub.history(sensor.sensor_id):
                # The history is kept on the monotonic clock; convert it back
                # to wall time so the export lines up with anything else.
                wall = wall_now - (monotonic_now - timestamp)
                writer.writerow(
                    [
                        datetime.fromtimestamp(wall).isoformat(timespec="seconds"),
                        sensor.sensor_id,
                        sensor.label,
                        f"{value:.2f}",
                        "C",
                    ]
                )
                written += 1
    return written
