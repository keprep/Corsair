"""Lighting and LCD page.

Drivers differ a lot in what they accept, so the widgets are built from what
the device actually reports: the mode list comes from the driver, the speed
selector only appears when ``set_color`` takes a speed, and the screen block
only when the device has one.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.device import ManagedDevice
from corsair_control.core.profile import DeviceConfig, LightingConfig, ScreenConfig
from corsair_control.ui.i18n import tr
from corsair_control.ui.theme import PALETTE, mix

MAX_COLOURS = 6
SPEEDS = ["slowest", "slower", "normal", "faster", "fastest"]
DIRECTIONS = ["forward", "backward"]
DEFAULT_COLOURS = [[240, 165, 0], [34, 196, 214], [139, 124, 246], [62, 207, 142]]


class ColourRow(QWidget):
    """A dynamic list of colour swatches."""

    changed = pyqtSignal()

    def __init__(self, colours: list[list[int]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.colours = [list(c) for c in colours] or [list(DEFAULT_COLOURS[0])]
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self._rebuild()

    def _rebuild(self) -> None:
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index, colour in enumerate(self.colours):
            button = QPushButton()
            button.setFixedSize(28, 28)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tr("Click to change, right-click to remove"))
            button.setStyleSheet(
                f"QPushButton {{ background: {QColor(*colour).name()};"
                f" border-radius: 7px; border: 1px solid {PALETTE.border}; }}"
            )
            button.clicked.connect(lambda _checked, i=index: self._edit(i))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda _pos, i=index: self._remove(i))
            self.layout_.addWidget(button)

        if len(self.colours) < MAX_COLOURS:
            add = QPushButton("+")
            add.setFixedSize(28, 28)
            add.clicked.connect(self._add)
            self.layout_.addWidget(add)
        self.layout_.addStretch(1)

    def _edit(self, index: int) -> None:
        current = QColor(*self.colours[index])
        colour = QColorDialog.getColor(current, self, tr("Pick colour"))
        if colour.isValid():
            self.colours[index] = [colour.red(), colour.green(), colour.blue()]
            self._rebuild()
            self.changed.emit()

    def _add(self) -> None:
        self.colours.append(list(DEFAULT_COLOURS[len(self.colours) % len(DEFAULT_COLOURS)]))
        self._rebuild()
        self.changed.emit()

    def _remove(self, index: int) -> None:
        if len(self.colours) <= 1:
            return
        del self.colours[index]
        self._rebuild()
        self.changed.emit()


class DeviceLighting(QFrame):
    applyRequested = pyqtSignal(str, str, str, list, object, object)
    screenRequested = pyqtSignal(str, str, str, str)

    def __init__(
        self, device: ManagedDevice, config: DeviceConfig, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMaximumWidth(960)
        self.device = device
        self.config = config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        title = QLabel(device.short_name)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        if not device.supports_lighting and not device.supports_screen:
            note = QLabel(tr("This device has no controllable lighting."))
            note.setObjectName("Faint")
            layout.addWidget(note)
            self.status = note
            return

        self.status = QLabel("")
        self.status.setObjectName("Faint")

        if device.supports_lighting:
            layout.addLayout(self._build_lighting())
            layout.addWidget(self.colours)
        if device.supports_screen:
            layout.addWidget(self._separator())
            layout.addLayout(self._build_screen())

        layout.addWidget(self.status)
        self._load_saved()

    # ------------------------------------------------------------------
    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {PALETTE.border};")
        return line

    def _build_lighting(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.channel_box = QComboBox()
        for channel in self.device.lighting_channels:
            self.channel_box.addItem(channel.label, channel.channel_id)
        self.channel_box.currentIndexChanged.connect(self._on_channel_changed)
        row.addWidget(self.channel_box, 1)

        row.addWidget(QLabel(tr("Lighting mode")))
        self.mode_box = QComboBox()
        row.addWidget(self.mode_box, 1)

        self.speed_label = QLabel(tr("Animation speed"))
        row.addWidget(self.speed_label)
        self.speed_box = QComboBox()
        for speed in SPEEDS:
            self.speed_box.addItem(tr(speed), speed)
        self.speed_box.setCurrentIndex(2)
        row.addWidget(self.speed_box)

        self.direction_label = QLabel(tr("Direction"))
        row.addWidget(self.direction_label)
        self.direction_box = QComboBox()
        for direction in DIRECTIONS:
            self.direction_box.addItem(tr(direction), direction)
        row.addWidget(self.direction_box)

        apply_button = QPushButton(tr("Apply"))
        apply_button.setObjectName("Accent")
        apply_button.clicked.connect(self._apply)
        row.addWidget(apply_button)

        self.colours = ColourRow([[240, 165, 0]])
        self._sync_modes()
        return row

    def _build_screen(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        label = QLabel(tr("Display"))
        label.setStyleSheet(f"color: {mix(PALETTE.text, PALETTE.accent, 0.4).name()};")
        row.addWidget(label)

        self.screen_channel = QComboBox()
        for channel in self.device.screen_channels:
            self.screen_channel.addItem(channel.label, channel.channel_id)
        row.addWidget(self.screen_channel)

        self.screen_mode = QComboBox()
        modes = self.device.screen_channels[0].modes if self.device.screen_channels else []
        for mode in modes:
            self.screen_mode.addItem(tr(mode), mode)
        self.screen_mode.currentIndexChanged.connect(self._sync_screen_inputs)
        row.addWidget(self.screen_mode)

        self.screen_value = QLineEdit()
        self.screen_value.setPlaceholderText(tr("Image or GIF path"))
        row.addWidget(self.screen_value, 2)

        self.screen_browse = QPushButton("…")
        self.screen_browse.setFixedWidth(34)
        self.screen_browse.clicked.connect(self._browse)
        row.addWidget(self.screen_browse)

        self.screen_number = QSpinBox()
        self.screen_number.setRange(0, 100)
        self.screen_number.setValue(80)
        row.addWidget(self.screen_number)

        apply_button = QPushButton(tr("Apply"))
        apply_button.clicked.connect(self._apply_screen)
        row.addWidget(apply_button)

        self._sync_screen_inputs()
        return row

    # ------------------------------------------------------------------
    def _current_channel(self):
        channel_id = self.channel_box.currentData()
        return next(
            (c for c in self.device.lighting_channels if c.channel_id == channel_id), None
        )

    def _sync_modes(self) -> None:
        channel = self._current_channel()
        self.mode_box.clear()
        for mode in channel.modes if channel else []:
            self.mode_box.addItem(mode, mode)

        supports_speed = bool(channel and channel.supports_speed)
        self.speed_box.setVisible(supports_speed)
        self.speed_label.setVisible(supports_speed)
        supports_direction = bool(channel and channel.supports_direction)
        self.direction_box.setVisible(supports_direction)
        self.direction_label.setVisible(supports_direction)

    def _on_channel_changed(self) -> None:
        self._sync_modes()
        self._load_saved()

    def _sync_screen_inputs(self) -> None:
        mode = self.screen_mode.currentData() or ""
        needs_file = mode in {"gif", "static", "image"}
        needs_number = mode in {"brightness", "orientation"}
        self.screen_value.setVisible(needs_file)
        self.screen_browse.setVisible(needs_file)
        self.screen_number.setVisible(needs_number)
        if mode == "orientation":
            self.screen_number.setRange(0, 270)
            self.screen_number.setSingleStep(90)
        elif mode == "brightness":
            self.screen_number.setRange(0, 100)
            self.screen_number.setSingleStep(10)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Image or GIF path"), str(Path.home()), "Images (*.png *.jpg *.jpeg *.gif)"
        )
        if path:
            self.screen_value.setText(path)

    def _load_saved(self) -> None:
        if not self.device.supports_lighting:
            return
        channel_id = self.channel_box.currentData()
        saved = self.config.lighting.get(channel_id)
        if saved is not None:
            index = self.mode_box.findData(saved.mode)
            if index >= 0:
                self.mode_box.setCurrentIndex(index)
            if saved.speed:
                index = self.speed_box.findData(saved.speed)
                if index >= 0:
                    self.speed_box.setCurrentIndex(index)
            if saved.colors:
                self.colours.colours = [list(c) for c in saved.colors]
                self.colours._rebuild()

    # ------------------------------------------------------------------
    def _apply(self) -> None:
        channel = self._current_channel()
        channel_id = self.channel_box.currentData()
        mode = self.mode_box.currentData() or "fixed"
        colours = [list(c) for c in self.colours.colours]
        speed = self.speed_box.currentData() if channel and channel.supports_speed else None
        direction = (
            self.direction_box.currentData() if channel and channel.supports_direction else None
        )
        self.config.lighting[channel_id] = LightingConfig(
            mode=mode, colors=colours, speed=speed, direction=direction
        )
        self.applyRequested.emit(
            self.device.key, channel_id, mode, colours, speed, direction
        )

    def _apply_screen(self) -> None:
        channel_id = self.screen_channel.currentData()
        mode = self.screen_mode.currentData() or "liquid"
        if mode in {"brightness", "orientation"}:
            value = str(self.screen_number.value())
        elif mode in {"gif", "static", "image"}:
            value = self.screen_value.text().strip()
        else:
            value = ""
        self.config.screens[channel_id] = ScreenConfig(mode=mode, value=value)
        self.screenRequested.emit(self.device.key, channel_id, mode, value)

    def show_result(self, error: str | None) -> None:
        self.status.setText(error or tr("Applied"))
        self.status.setStyleSheet(
            f"color: {PALETTE.bad};" if error else f"color: {PALETTE.good};"
        )


class LightingPage(QWidget):
    applyRequested = pyqtSignal(str, str, str, list, object, object)
    screenRequested = pyqtSignal(str, str, str, str)
    syncRequested = pyqtSignal()

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

        header = QHBoxLayout()
        title = QLabel(tr("Lighting"))
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.sync_button = QPushButton(tr("Apply to all devices"))
        self.sync_button.clicked.connect(self.syncRequested)
        header.addWidget(self.sync_button)
        self.layout_.addLayout(header)

        self._blocks = QVBoxLayout()
        self._blocks.setSpacing(12)
        self.layout_.addLayout(self._blocks)

    def rebuild(self, devices: list[ManagedDevice], configs: dict[str, DeviceConfig]) -> None:
        while self._blocks.count():
            item = self._blocks.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._widgets.clear()

        lit = [d for d in devices if d.supports_lighting or d.supports_screen]
        for device in lit:
            block = DeviceLighting(device, configs.get(device.key) or DeviceConfig())
            block.applyRequested.connect(self.applyRequested)
            block.screenRequested.connect(self.screenRequested)
            self._blocks.addWidget(block)
            self._widgets[device.key] = block

        self.sync_button.setEnabled(len(lit) > 1)
        if not lit:
            note = QLabel(tr("No device with controllable lighting was found."))
            note.setObjectName("Faint")
            self._blocks.addWidget(note)

    def first_settings(self) -> tuple[str, list, object, object] | None:
        """The lighting settings of the first device, for the sync button."""
        for block in self._widgets.values():
            if not block.device.supports_lighting:
                continue
            channel = block._current_channel()
            return (
                block.mode_box.currentData() or "fixed",
                [list(c) for c in block.colours.colours],
                block.speed_box.currentData() if channel and channel.supports_speed else None,
                (
                    block.direction_box.currentData()
                    if channel and channel.supports_direction
                    else None
                ),
            )
        return None

    def devices_with_lighting(self) -> list[tuple[str, list[str]]]:
        return [
            (key, [c.channel_id for c in block.device.lighting_channels])
            for key, block in self._widgets.items()
            if block.device.supports_lighting
        ]

    def show_result(self, device_key: str, error: str | None) -> None:
        widget = self._widgets.get(device_key)
        if widget is not None:
            widget.show_result(error)
