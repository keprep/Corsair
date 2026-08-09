"""Lighting page - static colours and whatever modes the driver reports."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.device import ManagedDevice
from corsair_control.core.profile import DeviceConfig, LightingConfig
from corsair_control.ui.i18n import tr


class DeviceLighting(QFrame):
    applyRequested = pyqtSignal(str, str, str, list)  # device, channel, mode, colours

    def __init__(
        self, device: ManagedDevice, config: DeviceConfig, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMaximumWidth(880)
        self.device = device
        self.config = config
        self._colour = QColor(240, 165, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel(device.short_name)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        if not device.supports_lighting:
            note = QLabel(tr("This device has no controllable lighting."))
            note.setObjectName("Faint")
            layout.addWidget(note)
            return

        row = QHBoxLayout()
        row.setSpacing(10)

        self.channel_box = QComboBox()
        for channel in device.lighting_channels:
            self.channel_box.addItem(channel.label, channel.channel_id)
        self.channel_box.currentIndexChanged.connect(self._sync_modes)
        row.addWidget(self.channel_box, 1)

        row.addWidget(QLabel(tr("Lighting mode")))
        self.mode_box = QComboBox()
        row.addWidget(self.mode_box, 1)

        self.colour_button = QPushButton(tr("Pick colour"))
        self.colour_button.clicked.connect(self._pick_colour)
        row.addWidget(self.colour_button)

        self.swatch = QFrame()
        self.swatch.setFixedSize(28, 28)
        row.addWidget(self.swatch)

        apply_button = QPushButton(tr("Apply"))
        apply_button.setObjectName("Accent")
        apply_button.clicked.connect(self._apply)
        row.addWidget(apply_button)

        layout.addLayout(row)

        self.status = QLabel("")
        self.status.setObjectName("Faint")
        layout.addWidget(self.status)

        self._sync_modes()
        self._load_saved()
        self._paint_swatch()

    # ------------------------------------------------------------------
    def _sync_modes(self) -> None:
        channel_id = self.channel_box.currentData()
        channel = next(
            (c for c in self.device.lighting_channels if c.channel_id == channel_id), None
        )
        self.mode_box.clear()
        for mode in (channel.modes if channel else []):
            self.mode_box.addItem(mode, mode)

    def _load_saved(self) -> None:
        channel_id = self.channel_box.currentData()
        saved = self.config.lighting.get(channel_id)
        if saved is None:
            return
        index = self.mode_box.findData(saved.mode)
        if index >= 0:
            self.mode_box.setCurrentIndex(index)
        if saved.colors:
            r, g, b = (list(saved.colors[0]) + [0, 0, 0])[:3]
            self._colour = QColor(int(r), int(g), int(b))

    def _paint_swatch(self) -> None:
        self.swatch.setStyleSheet(
            f"background: {self._colour.name()}; border-radius: 6px; border: 1px solid #2c313d;"
        )

    def _pick_colour(self) -> None:
        colour = QColorDialog.getColor(self._colour, self, tr("Pick colour"))
        if colour.isValid():
            self._colour = colour
            self._paint_swatch()

    def _apply(self) -> None:
        channel_id = self.channel_box.currentData()
        mode = self.mode_box.currentData() or "fixed"
        colours = [[self._colour.red(), self._colour.green(), self._colour.blue()]]
        self.config.lighting[channel_id] = LightingConfig(mode=mode, colors=colours)
        self.applyRequested.emit(self.device.key, channel_id, mode, colours)

    def show_result(self, error: str | None) -> None:
        self.status.setText(error or tr("Applied"))


class LightingPage(QWidget):
    applyRequested = pyqtSignal(str, str, str, list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self.layout_ = QVBoxLayout(body)
        self.layout_.setContentsMargins(20, 16, 20, 16)
        self.layout_.setSpacing(12)
        self.layout_.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._widgets: dict[str, DeviceLighting] = {}

    def rebuild(self, devices: list[ManagedDevice], configs: dict[str, DeviceConfig]) -> None:
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._widgets.clear()

        for device in devices:
            block = DeviceLighting(device, configs.get(device.key) or DeviceConfig())
            block.applyRequested.connect(self.applyRequested)
            self.layout_.addWidget(block)
            self._widgets[device.key] = block

        if not devices:
            note = QLabel(tr("No devices found"))
            note.setObjectName("Faint")
            self.layout_.addWidget(note)

    def show_result(self, device_key: str, error: str | None) -> None:
        widget = self._widgets.get(device_key)
        if widget is not None:
            widget.show_result(error)
