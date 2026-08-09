"""One page per detected device: channel cards on the left, curve editor on the right."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.device import ManagedDevice
from corsair_control.core.engine import DeviceSnapshot
from corsair_control.core.profile import DeviceConfig
from corsair_control.core.sensors import Sensor
from corsair_control.ui.i18n import tr
from corsair_control.ui.theme import PALETTE, temperature_colour
from corsair_control.ui.widgets.channel_card import ChannelCard
from corsair_control.ui.widgets.curve_panel import CurvePanel
from corsair_control.ui.widgets.gauge import Gauge

#: Cards reflow between one and three columns depending on the width the
#: splitter gives them.
MIN_CARD_WIDTH = 330
MAX_CARD_COLUMNS = 3


class DevicePage(QWidget):
    configChanged = pyqtSignal(str, str)  # device_key, channel_id
    overrideChanged = pyqtSignal(str, str, object)
    pumpModeChanged = pyqtSignal(str, str)
    calibrationRequested = pyqtSignal(str, str)

    def __init__(
        self,
        device: ManagedDevice,
        config: DeviceConfig,
        sensor_provider: Callable[[], list[Sensor]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.device = device
        self.config = config
        self._sensor_provider = sensor_provider
        self.cards: dict[str, ChannelCard] = {}
        self._card_order: list[ChannelCard] = []
        self._columns = 0
        self._gauges: dict[str, Gauge] = {}
        self._selected: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 12)
        outer.setSpacing(12)

        outer.addLayout(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self.grid = QGridLayout(container)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(container)
        splitter.addWidget(self.scroll)

        self.panel = CurvePanel()
        self.panel.changed.connect(self._on_panel_changed)
        self.panel.calibrationRequested.connect(self._on_calibration_requested)
        splitter.addWidget(self.panel)
        splitter.setSizes([620, 480])
        outer.addWidget(splitter, 1)

        self._build_cards()

    # ------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(14)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(self.device.short_name)
        title.setObjectName("SectionTitle")
        subtitle = QLabel(f"{self.device.driver} · {self.device.bus_info}")
        subtitle.setObjectName("Faint")
        text.addWidget(title)
        text.addWidget(subtitle)
        header.addLayout(text)
        header.addStretch(1)

        if self.device.pump_modes:
            header.addWidget(QLabel(tr("Pump")))
            self.pump_box = QComboBox()
            for mode in self.device.pump_modes:
                self.pump_box.addItem(tr(mode.capitalize()), mode)
            if self.config.pump_mode:
                index = self.pump_box.findData(self.config.pump_mode)
                if index >= 0:
                    self.pump_box.setCurrentIndex(index)
            self.pump_box.currentIndexChanged.connect(self._on_pump_mode)
            header.addWidget(self.pump_box)

        self.gauge_row = QHBoxLayout()
        self.gauge_row.setSpacing(8)
        header.addLayout(self.gauge_row)
        return header

    def _build_cards(self) -> None:
        for channel in self.device.channels:
            channel_config = self.config.channel(channel.channel_id)
            card = ChannelCard(
                self.device.key,
                channel.channel_id,
                channel.label,
                channel.kind,
                channel_config,
                self._sensor_provider,
                controllable=channel.controllable,
            )
            card.configChanged.connect(self._on_card_changed)
            card.overrideChanged.connect(self.overrideChanged)
            card.selected.connect(self._on_card_selected)
            self.cards[channel.channel_id] = card
            self._card_order.append(card)
        self._relayout_cards(force=True)

        first = next((c for c in self.device.channels if c.controllable), None)
        if first is not None:
            self._on_card_selected(self.device.key, first.channel_id)
        else:
            self.panel.unbind(tr("not controllable"))

    def _relayout_cards(self, *, force: bool = False) -> None:
        """Reflow the card grid so cards never get narrower than they need."""
        width = self.scroll.viewport().width() or self.width()
        columns = max(1, min(MAX_CARD_COLUMNS, width // MIN_CARD_WIDTH))
        if columns == self._columns and not force:
            return
        self._columns = columns
        for card in self._card_order:
            self.grid.removeWidget(card)
        for index, card in enumerate(self._card_order):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(MAX_CARD_COLUMNS):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_cards()

    # ------------------------------------------------------------------
    def _on_card_selected(self, device_key: str, channel_id: str) -> None:
        self._selected = channel_id
        for key, card in self.cards.items():
            card.set_selected(key == channel_id)
        channel = next(
            (c for c in self.device.channels if c.channel_id == channel_id), None
        )
        if channel is None:
            self.panel.unbind()
            return
        self.panel.bind(
            channel.label,
            self.config.channel(channel_id),
            is_pump=channel.is_pump,
            supports_hardware=self.device.supports_hardware_curves,
        )

    def _on_card_changed(self, device_key: str, channel_id: str) -> None:
        if channel_id == self._selected:
            self._on_card_selected(device_key, channel_id)
        card = self.cards.get(channel_id)
        if card is not None:
            card.preview.set_curve(card.config.curve)
        self.configChanged.emit(device_key, channel_id)

    def _on_panel_changed(self) -> None:
        if self._selected is None:
            return
        card = self.cards.get(self._selected)
        if card is not None:
            card.preview.update()
        self.configChanged.emit(self.device.key, self._selected)

    def _on_calibration_requested(self) -> None:
        if self._selected:
            self.calibrationRequested.emit(self.device.key, self._selected)

    def calibration_finished(self) -> None:
        self.panel.refresh_calibration()

    def _on_pump_mode(self, index: int) -> None:
        mode = self.pump_box.itemData(index)
        if mode:
            self.config.pump_mode = mode
            self.pumpModeChanged.emit(self.device.key, mode)

    # ------------------------------------------------------------------
    def set_config(self, config: DeviceConfig) -> None:
        """Rebind everything after a profile switch."""
        self.config = config
        for channel_id, card in self.cards.items():
            card.config = config.channel(channel_id)
            card.sync_from_config()
        if self._selected:
            self._on_card_selected(self.device.key, self._selected)

    def refresh_sensor_lists(self) -> None:
        for card in self.cards.values():
            card.refresh_sensor_list()

    def update_from(self, snapshot: DeviceSnapshot) -> None:
        self._update_gauges(snapshot)
        for channel in snapshot.channels:
            card = self.cards.get(channel.channel_id)
            if card is None:
                continue
            card.update_from(channel)
            if channel.channel_id == self._selected:
                self.panel.set_live(channel.sensor_value, channel.target_duty)

    def _update_gauges(self, snapshot: DeviceSnapshot) -> None:
        for name, value in snapshot.temperatures.items():
            gauge = self._gauges.get(name)
            if gauge is None:
                gauge = Gauge(title=name.replace(" temperature", ""), unit="°C")
                gauge.setFixedSize(104, 112)
                self._gauges[name] = gauge
                self.gauge_row.addWidget(gauge)
            gauge.set_value(value, "°C")

    def status_line(self, snapshot: DeviceSnapshot) -> str:
        if snapshot.error:
            return snapshot.error
        temps = ", ".join(f"{k}: {v:.1f} °C" for k, v in snapshot.temperatures.items())
        return temps or tr("Connected")


def temperature_style(value: float | None) -> str:
    colour = temperature_colour(value)
    return f"color: {colour.name()};" if value is not None else f"color: {PALETTE.text_faint};"
