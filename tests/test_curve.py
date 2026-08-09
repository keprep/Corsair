from corsair_control.core.curve import CurveEvaluator, CurvePoint, FanCurve, preset_curve


def test_interpolates_between_points():
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 100)])
    assert curve.duty_at(30) == 20
    assert curve.duty_at(70) == 100
    assert curve.duty_at(50) == 60


def test_holds_flat_outside_the_defined_range():
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 100)])
    assert curve.duty_at(0) == 20
    assert curve.duty_at(120) == 100


def test_points_are_sorted_and_deduplicated():
    curve = FanCurve([CurvePoint(70, 100), CurvePoint(30, 20), CurvePoint(30.2, 25)])
    assert [p.temp for p in curve.points] == [30.2, 70]


def test_values_are_clamped():
    point = CurvePoint(500, -20)
    assert point.temp == 100
    assert point.duty == 0


def test_move_point_stays_between_neighbours():
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(50, 50), CurvePoint(70, 100)])
    curve.move_point(1, 90, 60)
    assert curve.points[1].temp <= 69
    curve.move_point(1, 10, 60)
    assert curve.points[1].temp >= 31


def test_remove_point_keeps_at_least_two():
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 100)])
    assert curve.remove_point(0) is False
    assert len(curve.points) == 2


def test_round_trip_serialisation():
    curve = preset_curve("Silent")
    assert FanCurve.from_list(curve.to_list()).to_list() == curve.to_list()


def test_pump_presets_never_stall_the_pump():
    for name in ("Silent", "Balanced", "Performance", "Extreme"):
        curve = preset_curve(name, pump=True)
        assert min(p.duty for p in curve.points) >= 50


def test_evaluator_suppresses_small_changes():
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 100)])
    evaluator = CurveEvaluator(smoothing=1.0, hysteresis=3.0, min_step_seconds=0.0)

    assert evaluator.feed(40.0, curve, 0.0) == 40.0
    # +0.5 °C is one duty point - below the hysteresis threshold.
    assert evaluator.feed(40.5, curve, 1.0) is None
    # +5 °C is ten duty points and must get through.
    assert evaluator.feed(45.0, curve, 2.0) == 50.0


def test_evaluator_is_lazier_on_the_way_down():
    curve = FanCurve([CurvePoint(30, 0), CurvePoint(70, 100)])
    evaluator = CurveEvaluator(smoothing=1.0, hysteresis=4.0, min_step_seconds=0.0)
    evaluator.feed(50.0, curve, 0.0)
    # -1.6 °C -> -4 duty points: enough when rising, not enough when falling.
    assert evaluator.feed(48.4, curve, 1.0) is None
    assert evaluator.feed(51.6, curve, 2.0) is not None
