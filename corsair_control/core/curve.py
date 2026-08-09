"""Fan curve model, interpolation and the smoothing/hysteresis evaluator."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Iterable, Sequence

MIN_TEMP = 0.0
MAX_TEMP = 100.0


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


@dataclass(order=True)
class CurvePoint:
    temp: float
    duty: float

    def __post_init__(self) -> None:
        self.temp = clamp(float(self.temp), MIN_TEMP, MAX_TEMP)
        self.duty = clamp(float(self.duty), 0.0, 100.0)

    def as_tuple(self) -> tuple[float, float]:
        return (self.temp, self.duty)


@dataclass
class FanCurve:
    """A monotonically ordered list of temperature/duty pairs.

    Between two points the duty is interpolated linearly. Below the first and
    above the last point the curve is held flat, which keeps the behaviour
    predictable when a sensor briefly reports an out-of-range value.
    """

    points: list[CurvePoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.points:
            self.points = [CurvePoint(*p) for p in PRESETS["Balanced"]]
        self.normalise()

    # ------------------------------------------------------------------
    # structure
    # ------------------------------------------------------------------
    def normalise(self) -> None:
        """Sort by temperature and drop duplicate temperatures."""
        self.points.sort(key=lambda p: p.temp)
        deduped: list[CurvePoint] = []
        for point in self.points:
            if deduped and abs(deduped[-1].temp - point.temp) < 0.5:
                deduped[-1] = point
            else:
                deduped.append(point)
        self.points = deduped or [CurvePoint(*p) for p in PRESETS["Balanced"]]

    def add_point(self, temp: float, duty: float) -> CurvePoint:
        point = CurvePoint(temp, duty)
        self.points.append(point)
        self.normalise()
        return point

    def remove_point(self, index: int) -> bool:
        """Remove a point; refuses to shrink the curve below two points."""
        if len(self.points) <= 2 or not 0 <= index < len(self.points):
            return False
        del self.points[index]
        return True

    def move_point(self, index: int, temp: float, duty: float) -> None:
        """Move a point, keeping it between its neighbours.

        Constraining the point to its slot means dragging never reorders the
        list under the user's cursor, which is what makes the editor feel
        stable.
        """
        if not 0 <= index < len(self.points):
            return
        low = self.points[index - 1].temp + 1.0 if index > 0 else MIN_TEMP
        high = self.points[index + 1].temp - 1.0 if index < len(self.points) - 1 else MAX_TEMP
        self.points[index] = CurvePoint(clamp(temp, low, max(low, high)), duty)

    # ------------------------------------------------------------------
    # evaluation
    # ------------------------------------------------------------------
    def duty_at(self, temp: float) -> float:
        pts = self.points
        if temp <= pts[0].temp:
            return pts[0].duty
        if temp >= pts[-1].temp:
            return pts[-1].duty
        temps = [p.temp for p in pts]
        i = bisect.bisect_right(temps, temp)
        left, right = pts[i - 1], pts[i]
        span = right.temp - left.temp
        if span <= 0:
            return right.duty
        ratio = (temp - left.temp) / span
        return left.duty + ratio * (right.duty - left.duty)

    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------
    def to_list(self) -> list[list[float]]:
        return [[round(p.temp, 1), round(p.duty, 1)] for p in self.points]

    @classmethod
    def from_list(cls, data: Iterable[Sequence[float]] | None) -> "FanCurve":
        if not data:
            return cls()
        return cls([CurvePoint(float(t), float(d)) for t, d in data])

    def copy(self) -> "FanCurve":
        return FanCurve([CurvePoint(p.temp, p.duty) for p in self.points])


#: Ready-made curves offered in the UI. Deliberately conservative: every
#: preset keeps some airflow at idle because a fan that stops and restarts is
#: more audible than one that never stops.
PRESETS: dict[str, list[tuple[float, float]]] = {
    "Silent": [(20, 20), (40, 22), (55, 32), (65, 50), (75, 75), (85, 100)],
    "Balanced": [(20, 25), (40, 30), (50, 45), (60, 60), (70, 80), (80, 100)],
    "Performance": [(20, 40), (35, 50), (45, 65), (55, 80), (65, 95), (75, 100)],
    "Extreme": [(20, 60), (30, 70), (40, 85), (50, 95), (60, 100)],
}

#: Pumps like a narrower band - they are rarely the loudest part of a loop and
#: running one too slowly hurts the whole cooling chain.
PUMP_PRESETS: dict[str, list[tuple[float, float]]] = {
    "Silent": [(20, 50), (40, 55), (50, 65), (60, 80), (70, 100)],
    "Balanced": [(20, 60), (40, 70), (50, 80), (60, 90), (70, 100)],
    "Performance": [(20, 75), (40, 85), (50, 92), (60, 100)],
    "Extreme": [(20, 100), (60, 100)],
}


def preset_curve(name: str, *, pump: bool = False) -> FanCurve:
    table = PUMP_PRESETS if pump else PRESETS
    points = table.get(name) or table["Balanced"]
    return FanCurve([CurvePoint(t, d) for t, d in points])


class CurveEvaluator:
    """Turns a noisy temperature stream into a calm duty stream.

    Two mechanisms cooperate:

    * an exponential moving average over the *temperature*, so short spikes do
      not reach the curve at all;
    * hysteresis on the *output*, so a duty change is only committed once it
      differs from the last committed value by more than a threshold.

    Without both, fans audibly "breathe" around a curve knee.
    """

    def __init__(
        self,
        *,
        smoothing: float = 0.35,
        hysteresis: float = 3.0,
        min_step_seconds: float = 2.0,
    ) -> None:
        self.smoothing = clamp(smoothing, 0.01, 1.0)
        self.hysteresis = max(0.0, hysteresis)
        self.min_step_seconds = max(0.0, min_step_seconds)
        self._avg_temp: float | None = None
        self._committed: float | None = None
        self._last_change: float = 0.0

    @property
    def smoothed_temperature(self) -> float | None:
        return self._avg_temp

    @property
    def committed_duty(self) -> float | None:
        return self._committed

    def reset(self) -> None:
        self._avg_temp = None
        self._committed = None
        self._last_change = 0.0

    def feed(self, temp: float, curve: FanCurve, now: float) -> float | None:
        """Return a new duty to write, or ``None`` if nothing should change."""
        if self._avg_temp is None:
            self._avg_temp = temp
        else:
            self._avg_temp += self.smoothing * (temp - self._avg_temp)

        target = curve.duty_at(self._avg_temp)

        if self._committed is None:
            self._committed = target
            self._last_change = now
            return target

        delta = abs(target - self._committed)
        rising = target > self._committed
        # Let the fans spin up eagerly but wind down lazily: heat is the
        # thing we cannot take back.
        threshold = self.hysteresis if rising else self.hysteresis * 1.5
        if delta < threshold:
            return None
        if now - self._last_change < self.min_step_seconds and delta < 20:
            return None

        self._committed = target
        self._last_change = now
        return target
