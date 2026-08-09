"""Overview page: the numbers you want to see without clicking anything."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.engine import Snapshot
from corsair_control.core.sensors import Sensor
from corsair_control.ui.i18n import tr
from corsair_control.ui.icons import CATEGORY_GLYPHS
from corsair_control.ui.theme import PALETTE, temperature_colour
from corsair_control.ui.widgets.gauge import Bar, Gauge
from corsair_control.ui.widgets.graph import HistoryGraph

MAX_GAUGES = 6


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    layout.addWidget(label)
    return frame, layout


class ChannelRow(QWidget):
    """A single line in the "fans and pumps" table."""

    def __init__(self, label: str, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(10)

        self.name = QLabel(label)
        self.name.setMinimumWidth(210)
        layout.addWidget(self.name)

        self.rpm = QLabel("—")
        self.rpm.setMinimumWidth(78)
        self.rpm.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.rpm)

        self.bar = Bar()
        self.bar.set_colour(QColor(PALETTE.pump if kind == "pump" else PALETTE.accent))
        self.bar.setMinimumWidth(120)
        layout.addWidget(self.bar, 1)

        self.duty = QLabel("—")
        self.duty.setMinimumWidth(52)
        self.duty.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.duty)

        self.source = QLabel("")
        self.source.setObjectName("Faint")
        self.source.setMinimumWidth(150)
        layout.addWidget(self.source)

    def update_values(self, rpm, duty, sensor_value, sensor_label) -> None:
        self.rpm.setText("—" if rpm is None else f"{rpm:,.0f}".replace(",", " ") + " rpm")
        self.bar.set_value(duty)
        self.duty.setText("—" if duty is None else f"{duty:.0f} %")
        if sensor_value is None:
            self.source.setText(sensor_label)
        else:
            colour = temperature_colour(sensor_value).name()
            self.source.setText(f"{sensor_label}  ·  {sensor_value:.1f} °C")
            self.source.setStyleSheet(f"color: {colour}; font-size: 11px;")


class DashboardPage(QWidget):
    def __init__(
        self,
        sensor_provider: Callable[[], list[Sensor]],
        history_seconds: int = 300,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sensor_provider = sensor_provider
        self._gauges: dict[str, Gauge] = {}
        self._rows: dict[tuple[str, str], ChannelRow] = {}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        temps_card, temps_layout = _card(tr("Temperatures"))
        self.gauge_grid = QGridLayout()
        self.gauge_grid.setSpacing(10)
        temps_layout.addLayout(self.gauge_grid)
        layout.addWidget(temps_card)

        graph_card, graph_layout = _card(tr("History"))
        self.graph = HistoryGraph(window_seconds=history_seconds)
        graph_layout.addWidget(self.graph)
        layout.addWidget(graph_card, 1)

        fans_card, fans_layout = _card(tr("Fans and pumps"))
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(2)
        fans_layout.addLayout(self.rows_layout)
        self.empty_label = QLabel(tr("No devices found"))
        self.empty_label.setObjectName("Faint")
        fans_layout.addWidget(self.empty_label)
        layout.addWidget(fans_card)

    # ------------------------------------------------------------------
    def _interesting_sensors(self) -> list[Sensor]:
        sensors = self._sensor_provider()
        picked: list[Sensor] = []
        seen_categories: set[str] = set()
        # One representative per category first, then fill up with the rest so
        # the gauge row always shows CPU/GPU/liquid before board sensors.
        for category in ("cpu", "gpu", "liquid", "virtual", "storage"):
            for sensor in sensors:
                if sensor.category == category and category not in seen_categories:
                    picked.append(sensor)
                    seen_categories.add(category)
                    break
        for sensor in sensors:
            if len(picked) >= MAX_GAUGES:
                break
            if sensor not in picked and sensor.category in {"cpu", "gpu", "liquid"}:
                picked.append(sensor)
        return picked[:MAX_GAUGES]

    def refresh_sensors(self) -> None:
        sensors = self._interesting_sensors()
        for index, sensor in enumerate(sensors):
            gauge = self._gauges.get(sensor.sensor_id)
            if gauge is None:
                gauge = Gauge(
                    title=sensor.label.split(" · ")[0],
                    unit="°C",
                    glyph=CATEGORY_GLYPHS.get(sensor.category),
                )
                gauge.setMinimumSize(120, 132)
                self._gauges[sensor.sensor_id] = gauge
                self.gauge_grid.addWidget(gauge, 0, index)
        self.graph.set_series_keys([(s.sensor_id, s.label.split(" · ")[0]) for s in sensors])

    # ------------------------------------------------------------------
    def update_from(self, snapshot: Snapshot) -> None:
        for sensor_id, gauge in self._gauges.items():
            gauge.set_value(snapshot.sensors.get(sensor_id), "°C")
        self.graph.append(snapshot.sensors)

        seen: set[tuple[str, str]] = set()
        for device in snapshot.devices:
            for channel in device.channels:
                key = (device.key, channel.channel_id)
                seen.add(key)
                row = self._rows.get(key)
                if row is None:
                    row = ChannelRow(f"{device.name} · {channel.label}", channel.kind)
                    self._rows[key] = row
                    self.rows_layout.addWidget(row)
                row.update_values(
                    channel.rpm, channel.duty, channel.sensor_value, channel.sensor_label
                )

        for key in [k for k in self._rows if k not in seen]:
            row = self._rows.pop(key)
            self.rows_layout.removeWidget(row)
            row.deleteLater()

        self.empty_label.setVisible(not self._rows)
