"""Automatic profile switching.

A rule says "when this is true, use that profile". The engine evaluates every
rule once per cycle and activates the highest-priority match; when nothing
matches it falls back to the default profile, so leaving a game always takes
the machine back to the quiet profile on its own.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")
PROC_ROOT = Path("/proc")

POWER_AC = "ac"
POWER_BATTERY = "battery"


@dataclass
class Context:
    """Everything a rule can look at."""

    processes: frozenset[str] = frozenset()
    now: datetime = field(default_factory=datetime.now)
    power: str | None = None
    hottest: float | None = None


def _parse_time(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    try:
        hours, minutes = text.split(":")
        return int(hours) % 24, int(minutes) % 60
    except ValueError:
        return None


@dataclass
class Rule:
    """One condition set plus the profile it selects."""

    name: str = "Rule"
    profile: str = ""
    enabled: bool = True
    priority: int = 0
    process: str | None = None
    time_from: str | None = None
    time_to: str | None = None
    power: str | None = None
    temperature_above: float | None = None

    # ------------------------------------------------------------------
    @property
    def has_condition(self) -> bool:
        return any(
            (
                self.process,
                self.time_from and self.time_to,
                self.power,
                self.temperature_above is not None,
            )
        )

    def matches(self, context: Context) -> bool:
        if not self.enabled or not self.profile or not self.has_condition:
            return False
        if self.process and not self._process_matches(context.processes):
            return False
        if not self._time_matches(context.now):
            return False
        if self.power and context.power != self.power:
            return False
        if self.temperature_above is not None:
            if context.hottest is None or context.hottest < self.temperature_above:
                return False
        return True

    def _process_matches(self, processes: Iterable[str]) -> bool:
        needle = (self.process or "").strip().lower()
        if not needle:
            return True
        return any(needle in name for name in processes)

    def _time_matches(self, now: datetime) -> bool:
        start = _parse_time(self.time_from)
        end = _parse_time(self.time_to)
        if start is None or end is None:
            return True
        minutes = now.hour * 60 + now.minute
        start_minutes = start[0] * 60 + start[1]
        end_minutes = end[0] * 60 + end[1]
        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= minutes < end_minutes
        # Wraps past midnight, e.g. 22:00 - 07:00.
        return minutes >= start_minutes or minutes < end_minutes

    # ------------------------------------------------------------------
    def describe(self) -> str:
        parts = []
        if self.process:
            parts.append(f"process ~ {self.process}")
        if self.time_from and self.time_to:
            parts.append(f"{self.time_from}-{self.time_to}")
        if self.power:
            parts.append(self.power)
        if self.temperature_above is not None:
            parts.append(f"> {self.temperature_above:.0f} °C")
        return ", ".join(parts) or "always false"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "profile": self.profile,
            "enabled": self.enabled,
            "priority": self.priority,
            "process": self.process,
            "time_from": self.time_from,
            "time_to": self.time_to,
            "power": self.power,
            "temperature_above": self.temperature_above,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        temperature = data.get("temperature_above")
        return cls(
            name=str(data.get("name", "Rule")),
            profile=str(data.get("profile", "")),
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority", 0)),
            process=data.get("process") or None,
            time_from=data.get("time_from") or None,
            time_to=data.get("time_to") or None,
            power=data.get("power") or None,
            temperature_above=float(temperature) if temperature is not None else None,
        )


class ProcessWatcher:
    """Lists running process names, cached because /proc is not free."""

    def __init__(self, interval: float = 5.0) -> None:
        self.interval = interval
        self._names: frozenset[str] = frozenset()
        self._read_at = 0.0

    def names(self) -> frozenset[str]:
        now = time.monotonic()
        if now - self._read_at < self.interval and self._names:
            return self._names
        names: set[str] = set()
        try:
            entries = list(PROC_ROOT.iterdir())
        except OSError:
            return self._names
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                names.add((entry / "comm").read_text().strip().lower())
            except OSError:
                continue
        self._names = frozenset(names)
        self._read_at = now
        return self._names


def power_state() -> str | None:
    """``"ac"``, ``"battery"`` or ``None`` on a machine without a battery."""
    if not POWER_SUPPLY_ROOT.exists():
        return None
    has_battery = False
    for supply in POWER_SUPPLY_ROOT.iterdir():
        kind = None
        try:
            kind = (supply / "type").read_text().strip()
        except OSError:
            continue
        if kind == "Battery":
            has_battery = True
        elif kind == "Mains":
            try:
                online = (supply / "online").read_text().strip()
            except OSError:
                continue
            if online == "1":
                return POWER_AC
    return POWER_BATTERY if has_battery else None


class AutomationEngine:
    """Chooses a profile from the rules; keeps a manual override in mind."""

    def __init__(self, rules: list[Rule] | None = None, default_profile: str = "") -> None:
        self.rules: list[Rule] = rules or []
        self.default_profile = default_profile
        self.enabled = True
        self._processes = ProcessWatcher()
        self._last_choice: str | None = None
        self._active_rule: Rule | None = None

    @property
    def active_rule(self) -> Rule | None:
        return self._active_rule

    def context(self, hottest: float | None = None) -> Context:
        needs_processes = any(rule.process for rule in self.rules if rule.enabled)
        needs_power = any(rule.power for rule in self.rules if rule.enabled)
        return Context(
            processes=self._processes.names() if needs_processes else frozenset(),
            now=datetime.now(),
            power=power_state() if needs_power else None,
            hottest=hottest,
        )

    def choose(self, context: Context) -> str | None:
        """Return the profile that should be active, or ``None`` to leave it be."""
        if not self.enabled:
            return None
        matches = [rule for rule in self.rules if rule.matches(context)]
        if matches:
            winner = max(matches, key=lambda rule: (rule.priority, rule.name))
            self._active_rule = winner
            target = winner.profile
        else:
            self._active_rule = None
            target = self.default_profile
        if not target or target == self._last_choice:
            return None
        self._last_choice = target
        return target

    def note_manual_change(self, profile: str) -> None:
        """Remember a hand-picked profile so we do not fight the user."""
        self._last_choice = profile

    # ------------------------------------------------------------------
    def to_list(self) -> list[dict[str, Any]]:
        return [rule.to_dict() for rule in self.rules]

    def load(self, data: Iterable[dict[str, Any]] | None) -> None:
        self.rules = [Rule.from_dict(entry) for entry in (data or [])]
