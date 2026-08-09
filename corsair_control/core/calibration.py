"""Fan calibration: measure what a duty cycle actually does.

Percent is a lie that differs per fan. Sweeping a channel once and recording
the RPM at every step gives three things worth having:

* the duty at which the fan stops (so a curve can be kept above it);
* the duty needed to start it again from standstill, which is always higher;
* a duty <-> RPM mapping, so the UI can say "45 % is about 900 rpm".
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)

#: Below this the tacho signal is noise rather than rotation.
STOPPED_RPM = 60.0

DEFAULT_STEPS = (100, 90, 80, 70, 60, 50, 40, 30, 25, 20, 15, 10, 5, 0)


@dataclass
class CalibrationPoint:
    duty: float
    rpm: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.duty, self.rpm)


@dataclass
class ChannelCalibration:
    """Result of one sweep."""

    points: list[CalibrationPoint] = field(default_factory=list)
    stall_duty: float | None = None
    start_duty: float | None = None
    max_rpm: float = 0.0
    measured_at: float = 0.0

    # ------------------------------------------------------------------
    @property
    def ok(self) -> bool:
        return len(self.points) >= 2

    def rpm_at(self, duty: float) -> float | None:
        """Linear interpolation between measured points."""
        if not self.points:
            return None
        ordered = sorted(self.points, key=lambda p: p.duty)
        duties = [p.duty for p in ordered]
        if duty <= duties[0]:
            return ordered[0].rpm
        if duty >= duties[-1]:
            return ordered[-1].rpm
        index = bisect.bisect_right(duties, duty)
        left, right = ordered[index - 1], ordered[index]
        span = right.duty - left.duty
        if span <= 0:
            return right.rpm
        ratio = (duty - left.duty) / span
        return left.rpm + ratio * (right.rpm - left.rpm)

    def duty_for_rpm(self, rpm: float) -> float | None:
        """Inverse lookup - useful for entering a curve in RPM."""
        if not self.points:
            return None
        ordered = sorted(self.points, key=lambda p: p.rpm)
        if rpm <= ordered[0].rpm:
            return ordered[0].duty
        if rpm >= ordered[-1].rpm:
            return ordered[-1].duty
        for left, right in zip(ordered, ordered[1:]):
            if left.rpm <= rpm <= right.rpm:
                span = right.rpm - left.rpm
                if span <= 0:
                    return right.duty
                ratio = (rpm - left.rpm) / span
                return left.duty + ratio * (right.duty - left.duty)
        return ordered[-1].duty

    def safe_minimum(self, margin: float = 5.0) -> float | None:
        """A duty that keeps the fan turning, with a margin on the stall point."""
        if self.stall_duty is None:
            return None
        return min(100.0, self.stall_duty + margin)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "points": [[round(p.duty, 1), round(p.rpm)] for p in self.points],
            "stall_duty": self.stall_duty,
            "start_duty": self.start_duty,
            "max_rpm": self.max_rpm,
            "measured_at": self.measured_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ChannelCalibration | None":
        if not data:
            return None
        points = [CalibrationPoint(float(d), float(r)) for d, r in data.get("points") or []]
        if not points:
            return None
        return cls(
            points=points,
            stall_duty=data.get("stall_duty"),
            start_duty=data.get("start_duty"),
            max_rpm=float(data.get("max_rpm") or max(p.rpm for p in points)),
            measured_at=float(data.get("measured_at") or 0.0),
        )


class CalibrationCancelled(RuntimeError):
    pass


class CalibrationRunner:
    """Drives one channel through a sweep and records the result.

    The runner is deliberately synchronous: the caller owns the thread, which
    keeps cancellation and progress reporting simple.
    """

    def __init__(
        self,
        set_duty: Callable[[float], None],
        read_rpm: Callable[[], float | None],
        *,
        steps: Sequence[int] = DEFAULT_STEPS,
        settle_seconds: float = 3.5,
        samples: int = 3,
        sample_interval: float = 0.4,
    ) -> None:
        self.set_duty = set_duty
        self.read_rpm = read_rpm
        self.steps = list(steps)
        self.settle_seconds = settle_seconds
        self.samples = max(1, samples)
        self.sample_interval = sample_interval

    # ------------------------------------------------------------------
    def _wait(self, seconds: float, cancel: threading.Event | None) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise CalibrationCancelled()
            time.sleep(min(0.2, max(0.02, deadline - time.monotonic())))

    def _measure(self, cancel: threading.Event | None) -> float:
        readings: list[float] = []
        for _ in range(self.samples):
            value = self.read_rpm()
            if value is not None:
                readings.append(float(value))
            self._wait(self.sample_interval, cancel)
        if not readings:
            return 0.0
        return sum(readings) / len(readings)

    def run(
        self,
        progress: Callable[[float, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> ChannelCalibration:
        total = len(self.steps) + 8
        done = 0

        def tick(message: str) -> None:
            nonlocal done
            done += 1
            if progress is not None:
                progress(min(1.0, done / total), message)

        result = ChannelCalibration(measured_at=time.time())

        for duty in self.steps:
            self.set_duty(float(duty))
            self._wait(self.settle_seconds, cancel)
            rpm = self._measure(cancel)
            result.points.append(CalibrationPoint(float(duty), rpm))
            tick(f"{duty} % → {rpm:.0f} rpm")
            if rpm <= STOPPED_RPM and result.stall_duty is None and duty > 0:
                # The fan stopped: the previous step is the last one that held.
                result.stall_duty = float(duty)

        spinning = [p for p in result.points if p.rpm > STOPPED_RPM]
        if spinning:
            result.max_rpm = max(p.rpm for p in spinning)
            lowest = min(spinning, key=lambda p: p.duty)
            result.stall_duty = lowest.duty
        else:
            result.stall_duty = None

        # Starting from standstill needs more push than staying alive, and
        # that difference is what makes zero-RPM setups fail to restart.
        self.set_duty(0.0)
        self._wait(self.settle_seconds, cancel)
        for duty in range(5, 105, 5):
            self.set_duty(float(duty))
            self._wait(self.settle_seconds * 0.6, cancel)
            rpm = self._measure(cancel)
            if rpm > STOPPED_RPM:
                result.start_duty = float(duty)
                tick(f"start at {duty} %")
                break
        tick("done")
        return result
