from datetime import datetime

from corsair_control.core.automation import (
    POWER_AC,
    POWER_BATTERY,
    AutomationEngine,
    Context,
    Rule,
)


def ctx(**kwargs):
    base = dict(processes=frozenset(), now=datetime(2026, 8, 9, 12, 0), power=None, hottest=None)
    base.update(kwargs)
    return Context(**base)


def test_rule_without_condition_never_matches():
    rule = Rule(name="empty", profile="Silent")
    assert rule.has_condition is False
    assert rule.matches(ctx()) is False


def test_process_condition():
    rule = Rule(profile="Performance", process="steam")
    assert rule.matches(ctx(processes=frozenset({"steam", "bash"})))
    assert not rule.matches(ctx(processes=frozenset({"bash"})))


def test_process_condition_is_a_substring_match():
    rule = Rule(profile="Performance", process="cyber")
    assert rule.matches(ctx(processes=frozenset({"cyberpunk2077.exe"})))


def test_time_window_within_a_day():
    rule = Rule(profile="Silent", time_from="09:00", time_to="17:00")
    assert rule.matches(ctx(now=datetime(2026, 8, 9, 12, 0)))
    assert not rule.matches(ctx(now=datetime(2026, 8, 9, 18, 0)))


def test_time_window_across_midnight():
    rule = Rule(profile="Silent", time_from="22:00", time_to="07:00")
    assert rule.matches(ctx(now=datetime(2026, 8, 9, 23, 30)))
    assert rule.matches(ctx(now=datetime(2026, 8, 9, 3, 0)))
    assert not rule.matches(ctx(now=datetime(2026, 8, 9, 12, 0)))


def test_power_condition():
    rule = Rule(profile="Silent", power=POWER_BATTERY)
    assert rule.matches(ctx(power=POWER_BATTERY))
    assert not rule.matches(ctx(power=POWER_AC))


def test_temperature_condition():
    rule = Rule(profile="Performance", temperature_above=70)
    assert rule.matches(ctx(hottest=75))
    assert not rule.matches(ctx(hottest=65))
    assert not rule.matches(ctx(hottest=None))


def test_conditions_are_combined_with_and():
    rule = Rule(profile="Performance", process="steam", temperature_above=70)
    assert not rule.matches(ctx(processes=frozenset({"steam"}), hottest=50))
    assert rule.matches(ctx(processes=frozenset({"steam"}), hottest=80))


def test_engine_picks_the_highest_priority_match():
    engine = AutomationEngine(
        rules=[
            Rule(name="night", profile="Silent", time_from="00:00", time_to="23:59", priority=1),
            Rule(name="game", profile="Performance", process="steam", priority=5),
        ],
        default_profile="Balanced",
    )
    assert engine.choose(ctx(processes=frozenset({"steam"}))) == "Performance"
    assert engine.active_rule.name == "game"


def test_engine_falls_back_to_the_default_profile():
    engine = AutomationEngine(
        rules=[Rule(name="game", profile="Performance", process="steam")],
        default_profile="Balanced",
    )
    assert engine.choose(ctx(processes=frozenset({"steam"}))) == "Performance"
    assert engine.choose(ctx(processes=frozenset())) == "Balanced"


def test_engine_does_not_repeat_the_same_choice():
    engine = AutomationEngine(
        rules=[Rule(name="game", profile="Performance", process="steam")],
        default_profile="Balanced",
    )
    assert engine.choose(ctx(processes=frozenset({"steam"}))) == "Performance"
    assert engine.choose(ctx(processes=frozenset({"steam"}))) is None


def test_manual_change_is_respected():
    engine = AutomationEngine(
        rules=[Rule(name="game", profile="Performance", process="steam")],
        default_profile="Balanced",
    )
    engine.note_manual_change("Performance")
    assert engine.choose(ctx(processes=frozenset({"steam"}))) is None


def test_disabled_engine_chooses_nothing():
    engine = AutomationEngine(rules=[Rule(profile="X", process="a")], default_profile="Balanced")
    engine.enabled = False
    assert engine.choose(ctx(processes=frozenset({"a"}))) is None


def test_rule_round_trip():
    rule = Rule(name="n", profile="p", process="x", time_from="22:00", time_to="07:00")
    assert Rule.from_dict(rule.to_dict()) == rule
