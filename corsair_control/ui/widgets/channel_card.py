"""One card per fan or pump channel."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.engine import ChannelSnapshot
from corsair_control.core.profile import MODE_CURVE, MODE_FIXED, MODE_MANUAL, ChannelConfig
from corsair_control.core.sensors import Sensor
from corsair_control.ui.i18n import tr
from corsair_control.ui.icons import glyph_pixmap
from corsair_control.ui.theme import PALETTE, mix
from corsair_control.ui.widgets.curve_editor import CurveWidget
from corsair_control.ui.widgets.gauge import Bar
from corsair_control.ui.widgets.sensor_selector import SensorSelector

MODE_LABELS = [
    (MODE_CURVE, "Curve"),
    (MODE_FIXED, "Fixed"),
    (MODE_MANUAL, "Manual"),
]


class ChannelCard(QFrame):
    """Live readout plus the two controls that matter most: mode and source."""

    configChanged = pyqtSignal(str, str)  # device_key, channel_id
    selected = pyqtSignal(str, str)
    overrideChanged = pyqtSignal(str, str, object)  # duty or None

    def __init__(
        self,
        device_key: str,
        channel_id: str,
        label: str,
        kind: str,
        config: ChannelConfig,
        sensor_provider: Callable[[], list[Sensor]],
        *,
        controllable: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.device_key = device_key
        self.channel_id = channel_id
        self.kind = kind
        self.config = config
        self.controllable = controllable
        self._sensor_provider = sensor_provider
        self._is_selected = False

        accent = PALETTE.pump if kind == "pump" else PALETTE.accent
        self.accent = accent
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(8)

        self.icon = QLabel()
        self.icon.setPixmap(glyph_pixmap("pump" if kind == "pump" else "fan", accent, 18))
        self.icon.setFixedWidth(20)
        header.addWidget(self.icon)

        self.title = QLabel(label)
        self.title.setObjectName("CardTitle")
        badge = QLabel(tr("Pump") if kind == "pump" else tr("Fan"))
        badge.setStyleSheet(
            f"color:{accent}; font-size:10px; font-weight:700; letter-spacing:0.4px;"
            f"background:{mix(PALETTE.surface_alt, accent, 0.18).name()};"
            f"border-radius:6px; padding:2px 7px;"
        )
        header.addWidget(self.title)
        header.addWidget(badge)
        header.addStretch(1)
        self.rpm_label = QLabel("— rpm")
        self.rpm_label.setObjectName("Metric")
        header.addWidget(self.rpm_label)
        layout.addLayout(header)

        self.bar = Bar()
        self.bar.set_colour(QColor(accent))
        layout.addWidget(self.bar)

        duty_row = QHBoxLayout()
        self.duty_label = QLabel("—")
        self.duty_label.setObjectName("Faint")
        self.target_label = QLabel("")
        self.target_label.setObjectName("Faint")
        self.target_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        duty_row.addWidget(self.duty_label)
        duty_row.addStretch(1)
        duty_row.addWidget(self.target_label)
        layout.addLayout(duty_row)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.mode_box = QComboBox()
        for value, text in MODE_LABELS:
            self.mode_box.addItem(tr(text), value)
        self.mode_box.setCurrentIndex(
            max(0, [m for m, _ in MODE_LABELS].index(config.mode))
            if config.mode in [m for m, _ in MODE_LABELS]
            else 0
        )
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        controls.addWidget(self.mode_box, 1)

        self.sensor_box = SensorSelector(config, sensor_provider)
        self.sensor_box.changed.connect(
            lambda: self.configChanged.emit(self.device_key, self.channel_id)
        )
        controls.addWidget(self.sensor_box, 2)
        layout.addLayout(controls)

        self.preview = CurveWidget(config.curve, interactive=False, compact=True)
        self.preview.set_accent(QColor(accent))
        self.preview.setFixedHeight(74)
        layout.addWidget(self.preview)

        self.slider_row = QWidget()
        slider_layout = QHBoxLayout(self.slider_row)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(8)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(config.fixed_duty))
        self.slider.valueChanged.connect(self._on_slider)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider_value = QLabel(f"{int(config.fixed_duty)} %")
        self.slider_value.setMinimumWidth(42)
        self.slider_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        slider_layout.addWidget(self.slider, 1)
        slider_layout.addWidget(self.slider_value)
        layout.addWidget(self.slider_row)

        if not controllable:
            self.mode_box.setEnabled(False)
            self.sensor_box.setEnabled(False)
            self.slider.setEnabled(False)
            note = QLabel(tr("not controllable"))
            note.setObjectName("Faint")
            layout.addWidget(note)

        self.refresh_sensor_list()
        self._sync_visibility()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------
    def refresh_sensor_list(self) -> None:
        self.sensor_box.set_config(self.config)

    def set_selected(self, selected: bool) -> None:
        if selected == self._is_selected:
            return
        self._is_selected = selected
        if selected:
            self.setStyleSheet(
                f"#Card {{ border: 1px solid {self.accent};"
                f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                f" stop:0 {mix(PALETTE.surface_alt, self.accent, 0.10).name()},"
                f" stop:1 {PALETTE.surface_alt}); }}"
            )
        else:
            self.setStyleSheet("")

    # ------------------------------------------------------------------
    def _sync_visibility(self) -> None:
        mode = self.config.mode
        self.preview.setVisible(mode == MODE_CURVE)
        self.slider_row.setVisible(mode == MODE_FIXED)

    def _on_mode_changed(self, index: int) -> None:
        self.config.mode = self.mode_box.itemData(index) or MODE_CURVE
        self._sync_visibility()
        self.configChanged.emit(self.device_key, self.channel_id)

    def _on_slider(self, value: int) -> None:
        self.slider_value.setText(f"{value} %")
        self.config.fixed_duty = float(value)
        if self.slider.isSliderDown():
            # While dragging, push the value straight to the hardware so the
            # user hears the effect; the profile is only written on release.
            self.overrideChanged.emit(self.device_key, self.channel_id, float(value))
        else:
            self.configChanged.emit(self.device_key, self.channel_id)

    def _on_slider_released(self) -> None:
        self.overrideChanged.emit(self.device_key, self.channel_id, None)
        self.configChanged.emit(self.device_key, self.channel_id)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.selected.emit(self.device_key, self.channel_id)
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    def update_from(self, snapshot: ChannelSnapshot) -> None:
        rpm = snapshot.rpm
        self.rpm_label.setText("— rpm" if rpm is None else f"{rpm:,.0f} rpm".replace(",", " "))

        duty = snapshot.duty
        self.bar.set_value(duty)
        self.duty_label.setText("—" if duty is None else f"{duty:.0f} %")

        parts = []
        if snapshot.sensor_value is not None:
            parts.append(f"{snapshot.sensor_value:.1f} °C")
        if snapshot.target_duty is not None:
            parts.append(f"{tr('Target')} {snapshot.target_duty:.0f} %")
        self.target_label.setText("  ·  ".join(parts))

        if self.config.mode == MODE_CURVE:
            self.preview.set_live(snapshot.sensor_value, snapshot.target_duty)

    def sync_from_config(self) -> None:
        """Re-read the config object after an external change (profile switch)."""
        self.mode_box.blockSignals(True)
        index = self.mode_box.findData(self.config.mode)
        if index >= 0:
            self.mode_box.setCurrentIndex(index)
        self.mode_box.blockSignals(False)

        self.slider.blockSignals(True)
        self.slider.setValue(int(self.config.fixed_duty))
        self.slider_value.setText(f"{int(self.config.fixed_duty)} %")
        self.slider.blockSignals(False)

        self.preview.set_curve(self.config.curve)
        self.refresh_sensor_list()
        self._sync_visibility()
