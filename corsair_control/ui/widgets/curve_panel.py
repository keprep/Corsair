"""The editing panel that sits next to the channel cards."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.curve import PRESETS, preset_curve
from corsair_control.core.profile import MODE_CURVE, ChannelConfig
from corsair_control.ui.i18n import tr
from corsair_control.ui.theme import PALETTE
from corsair_control.ui.widgets.curve_editor import CurveWidget


class CurvePanel(QFrame):
    """Curve plus the per-channel limits."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._config: ChannelConfig | None = None
        self._is_pump = False
        self._supports_hardware = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.heading = QLabel(tr("Curve editor"))
        self.heading.setObjectName("SectionTitle")
        layout.addWidget(self.heading)

        self.subheading = QLabel(tr("Drag points, double-click to add, right-click to remove."))
        self.subheading.setObjectName("Faint")
        self.subheading.setWordWrap(True)
        layout.addWidget(self.subheading)

        self.editor = CurveWidget()
        self.editor.curveChanged.connect(self._on_curve_changed)
        layout.addWidget(self.editor, 1)

        presets = QHBoxLayout()
        presets.setSpacing(8)
        presets.addWidget(QLabel(tr("Preset")))
        self.preset_box = QComboBox()
        for name in PRESETS:
            self.preset_box.addItem(tr(name), name)
        presets.addWidget(self.preset_box, 1)
        self.apply_preset_button = QPushButton(tr("Apply preset"))
        self.apply_preset_button.clicked.connect(self._apply_preset)
        presets.addWidget(self.apply_preset_button)
        layout.addLayout(presets)

        limits = QHBoxLayout()
        limits.setSpacing(10)
        limits.addWidget(QLabel(tr("Minimum")))
        self.min_spin = QSpinBox()
        self.min_spin.setRange(0, 100)
        self.min_spin.setSuffix(" %")
        self.min_spin.valueChanged.connect(self._on_limits)
        limits.addWidget(self.min_spin)
        limits.addSpacing(6)
        limits.addWidget(QLabel(tr("Maximum")))
        self.max_spin = QSpinBox()
        self.max_spin.setRange(0, 100)
        self.max_spin.setSuffix(" %")
        self.max_spin.valueChanged.connect(self._on_limits)
        limits.addWidget(self.max_spin)
        limits.addStretch(1)
        layout.addLayout(limits)

        self.zero_rpm = QCheckBox(tr("Allow zero RPM"))
        self.zero_rpm.stateChanged.connect(self._on_flags)
        layout.addWidget(self.zero_rpm)

        self.offload = QCheckBox(tr("Run curve on the device"))
        self.offload.setToolTip(
            tr(
                "The curve is written to the controller once and keeps running "
                "even when this application is not."
            )
        )
        self.offload.stateChanged.connect(self._on_flags)
        layout.addWidget(self.offload)

        self.placeholder = QLabel(tr("Channel"))
        self.placeholder.setObjectName("Faint")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.placeholder)

        self.setEnabled(False)

    # ------------------------------------------------------------------
    def bind(
        self,
        title: str,
        config: ChannelConfig,
        *,
        is_pump: bool,
        supports_hardware: bool,
    ) -> None:
        self._config = None  # suppress change signals while wiring up
        self._is_pump = is_pump
        self._supports_hardware = supports_hardware

        self.heading.setText(f"{tr('Curve editor')} · {title}")
        self.editor.set_curve(config.curve)
        self.editor.set_accent(QColor(PALETTE.pump if is_pump else PALETTE.accent))
        self.min_spin.setValue(int(config.min_duty))
        self.max_spin.setValue(int(config.max_duty))
        self.zero_rpm.setChecked(config.allow_zero_rpm)
        self.offload.setChecked(config.offload_to_hardware)
        self.offload.setEnabled(supports_hardware)
        self.offload.setVisible(supports_hardware)
        self.placeholder.setVisible(False)

        self._config = config
        self.setEnabled(config.mode == MODE_CURVE)
        if config.mode != MODE_CURVE:
            self.placeholder.setVisible(True)
            self.placeholder.setText(tr("Switch this channel to curve mode to edit it."))

    def unbind(self, message: str = "") -> None:
        self._config = None
        self.setEnabled(False)
        self.placeholder.setVisible(True)
        self.placeholder.setText(message or tr("Channel"))

    def set_live(self, temp: float | None, duty: float | None) -> None:
        self.editor.set_live(temp, duty)

    # ------------------------------------------------------------------
    def _on_curve_changed(self) -> None:
        if self._config is None:
            return
        self.changed.emit()

    def _apply_preset(self) -> None:
        if self._config is None:
            return
        name = self.preset_box.currentData() or "Balanced"
        curve = preset_curve(name, pump=self._is_pump)
        self._config.curve = curve
        self.editor.set_curve(curve)
        self.changed.emit()

    def _on_limits(self) -> None:
        if self._config is None:
            return
        if self.min_spin.value() > self.max_spin.value():
            self.max_spin.setValue(self.min_spin.value())
            return
        self._config.min_duty = float(self.min_spin.value())
        self._config.max_duty = float(self.max_spin.value())
        self.changed.emit()

    def _on_flags(self) -> None:
        if self._config is None:
            return
        self._config.allow_zero_rpm = self.zero_rpm.isChecked()
        self._config.offload_to_hardware = self.offload.isChecked() and self._supports_hardware
        self.changed.emit()
