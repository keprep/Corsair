import threading

from corsair_control.core.calibration import (
    CalibrationCancelled,
    CalibrationPoint,
    CalibrationRunner,
    ChannelCalibration,
)


class FakeFan:
    """A fan that stops below 20 % and needs 30 % to start from standstill."""

    def __init__(self, stall=20, start=30, top_rpm=1800):
        self.stall = stall
        self.start = start
        self.top_rpm = top_rpm
        self.duty = 0.0
        self.spinning = False

    def set_duty(self, duty):
        self.duty = duty
        if self.spinning and duty < self.stall:
            self.spinning = False
        elif not self.spinning and duty >= self.start:
            self.spinning = True

    def rpm(self):
        if not self.spinning:
            return 0.0
        return self.top_rpm * self.duty / 100.0


def run(fan, **kwargs):
    runner = CalibrationRunner(
        fan.set_duty,
        fan.rpm,
        settle_seconds=0.0,
        samples=1,
        sample_interval=0.0,
        **kwargs,
    )
    return runner.run()


def test_sweep_records_a_point_per_step():
    fan = FakeFan()
    result = run(fan)
    assert len(result.points) == 14
    assert result.points[0].duty == 100


def test_stall_and_start_duty_are_found():
    fan = FakeFan(stall=20, start=30)
    result = run(fan)
    assert result.stall_duty == 20
    assert result.start_duty == 30


def test_max_rpm_is_measured():
    fan = FakeFan(top_rpm=2000)
    result = run(fan)
    assert result.max_rpm == 2000


def test_cancel_raises():
    fan = FakeFan()
    cancel = threading.Event()
    cancel.set()
    runner = CalibrationRunner(fan.set_duty, fan.rpm, settle_seconds=0.1, samples=1)
    try:
        runner.run(cancel=cancel)
    except CalibrationCancelled:
        pass
    else:  # pragma: no cover - would be a bug
        raise AssertionError("cancel was ignored")


def test_progress_is_reported():
    fan = FakeFan()
    seen = []
    runner = CalibrationRunner(
        fan.set_duty, fan.rpm, settle_seconds=0.0, samples=1, sample_interval=0.0
    )
    runner.run(progress=lambda fraction, message: seen.append(fraction))
    assert seen and seen[-1] <= 1.0
    assert seen == sorted(seen)


def test_rpm_interpolation():
    calibration = ChannelCalibration(
        points=[CalibrationPoint(0, 0), CalibrationPoint(50, 1000), CalibrationPoint(100, 2000)]
    )
    assert calibration.rpm_at(25) == 500
    assert calibration.rpm_at(-10) == 0
    assert calibration.rpm_at(200) == 2000


def test_duty_for_rpm_is_the_inverse():
    calibration = ChannelCalibration(
        points=[CalibrationPoint(0, 0), CalibrationPoint(100, 2000)]
    )
    assert calibration.duty_for_rpm(1000) == 50


def test_safe_minimum_adds_a_margin():
    calibration = ChannelCalibration(points=[CalibrationPoint(0, 0)], stall_duty=20)
    assert calibration.safe_minimum(margin=5) == 25


def test_round_trip():
    original = ChannelCalibration(
        points=[CalibrationPoint(0, 0), CalibrationPoint(100, 1500)],
        stall_duty=15,
        start_duty=25,
        max_rpm=1500,
    )
    restored = ChannelCalibration.from_dict(original.to_dict())
    assert restored is not None
    assert restored.stall_duty == 15
    assert restored.start_duty == 25
    assert [p.as_tuple() for p in restored.points] == [(0.0, 0.0), (100.0, 1500.0)]


def test_from_dict_of_nothing():
    assert ChannelCalibration.from_dict(None) is None
    assert ChannelCalibration.from_dict({}) is None
