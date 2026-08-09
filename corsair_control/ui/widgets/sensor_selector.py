"""Picker for a channel's temperature sources.

A curve can be driven by several sensors at once - "whichever of CPU and GPU
is hotter" is the setting most people actually want, and a single combo box
cannot express it. This is a button showing a summary that opens a menu of
checkable sensors plus the mixing mode.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QPushButton, QWidget

from corsair_control.core.profile import (
    SOURCE_AVERAGE,
    SOURCE_MAX,
    SOURCE_WEIGHTED,
    ChannelConfig,
)
from corsair_control.core.sensors import Sensor
from corsair_control.ui.i18n import tr

MODE_LABELS = {
    SOURCE_MAX: "Hottest",
    SOURCE_AVERAGE: "Average",
    SOURCE_WEIGHTED: "Weighted",
}

CATEGORY_LABELS = {
    "cpu": "CPU",
    "gpu": "GPU",
    "liquid": "Liquid",
    "virtual": "Combined",
    "storage": "Storage",
    "board": "Mainboard",
    "other": "Other",
}


class SensorSelector(QPushButton):
    """Summary button plus a checkable menu."""

    changed = pyqtSignal()

    def __init__(
        self,
        config: ChannelConfig,
        provider: Callable[[], list[Sensor]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._provider = provider
        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self.setMenu(self._menu)
        self.refresh()

    # ------------------------------------------------------------------
    def set_config(self, config: ChannelConfig) -> None:
        self.config = config
        self.refresh()

    def refresh(self) -> None:
        self.setText(self.summary())
        self.setToolTip(self.details())

    def summary(self) -> str:
        sensors = {s.sensor_id: s for s in self._provider()}
        labels = []
        for source in self.config.sources:
            sensor = sensors.get(source.sensor_id)
            labels.append(
                sensor.label.split(" · ")[0] if sensor else source.sensor_id.split(":")[-1]
            )
        if not labels:
            return tr("Pick a sensor")
        if len(labels) == 1:
            return labels[0]
        return f"{' + '.join(labels)} · {tr(MODE_LABELS[self.config.source_mode])}"

    def details(self) -> str:
        sensors = {s.sensor_id: s for s in self._provider()}
        lines = [
            sensors[s.sensor_id].label if s.sensor_id in sensors else s.sensor_id
            for s in self.config.sources
        ]
        return "\n".join(lines) or tr("Pick a sensor")

    # ------------------------------------------------------------------
    def _rebuild_menu(self) -> None:
        self._menu.clear()
        selected = set(self.config.sensor_ids)

        by_category: dict[str, list[Sensor]] = {}
        for sensor in self._provider():
            by_category.setdefault(sensor.category, []).append(sensor)

        for category, sensors in by_category.items():
            section = self._menu.addSection(tr(CATEGORY_LABELS.get(category, category)))
            section.setEnabled(False)
            for sensor in sensors:
                action = QAction(sensor.label, self._menu)
                action.setCheckable(True)
                action.setChecked(sensor.sensor_id in selected)
                action.toggled.connect(
                    lambda checked, sid=sensor.sensor_id: self._toggle(sid, checked)
                )
                self._menu.addAction(action)

        self._menu.addSeparator()
        mode_section = self._menu.addSection(tr("When several are selected"))
        mode_section.setEnabled(False)
        group = QActionGroup(self._menu)
        group.setExclusive(True)
        for mode, label in MODE_LABELS.items():
            action = QAction(tr(label), self._menu)
            action.setCheckable(True)
            action.setChecked(self.config.source_mode == mode)
            action.triggered.connect(lambda _checked, m=mode: self._set_mode(m))
            group.addAction(action)
            self._menu.addAction(action)

    def _toggle(self, sensor_id: str, checked: bool) -> None:
        ids = self.config.sensor_ids
        if checked and sensor_id not in ids:
            ids.append(sensor_id)
        elif not checked and sensor_id in ids:
            ids.remove(sensor_id)
        else:
            return
        self.config.set_sensor_ids(ids)
        self.refresh()
        self.changed.emit()

    def _set_mode(self, mode: str) -> None:
        if mode == self.config.source_mode:
            return
        self.config.source_mode = mode
        self.refresh()
        self.changed.emit()
