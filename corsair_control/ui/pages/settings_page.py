"""Settings page."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.config import Settings, config_dir
from corsair_control.ui.i18n import tr


def _card(title: str) -> tuple[QFrame, QFormLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setMaximumWidth(760)
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(10)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    outer.addWidget(label)
    form = QFormLayout()
    form.setSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    outer.addLayout(form)
    return frame, form


class SettingsPage(QWidget):
    settingsChanged = pyqtSignal()
    rescanRequested = pyqtSignal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        general, form = _card(tr("Settings"))

        self.poll = QDoubleSpinBox()
        self.poll.setRange(0.5, 10.0)
        self.poll.setSingleStep(0.5)
        self.poll.setDecimals(1)
        self.poll.setValue(settings.poll_interval)
        self.poll.valueChanged.connect(self._changed)
        form.addRow(tr("Polling interval (seconds)"), self.poll)

        self.history = QSpinBox()
        self.history.setRange(60, 3600)
        self.history.setSingleStep(60)
        self.history.setValue(settings.history_seconds)
        self.history.valueChanged.connect(self._changed)
        form.addRow(tr("History length (seconds)"), self.history)

        self.apply_on_start = QCheckBox()
        self.apply_on_start.setChecked(settings.apply_on_start)
        self.apply_on_start.stateChanged.connect(self._changed)
        form.addRow(tr("Apply profile on start"), self.apply_on_start)

        self.start_minimised = QCheckBox()
        self.start_minimised.setChecked(settings.start_minimised)
        self.start_minimised.stateChanged.connect(self._changed)
        form.addRow(tr("Start minimised to tray"), self.start_minimised)

        self.close_to_tray = QCheckBox()
        self.close_to_tray.setChecked(settings.close_to_tray)
        self.close_to_tray.stateChanged.connect(self._changed)
        form.addRow(tr("Close to tray"), self.close_to_tray)

        self.language = QComboBox()
        for value, label in (("system", tr("System")), ("de", tr("German")), ("en", tr("English"))):
            self.language.addItem(label, value)
        index = self.language.findData(settings.extra.get("language", "system"))
        if index >= 0:
            self.language.setCurrentIndex(index)
        self.language.currentIndexChanged.connect(self._changed)
        form.addRow(tr("Language"), self.language)

        hint = QLabel(tr("Restart required for language changes."))
        hint.setObjectName("Faint")
        general.layout().addWidget(hint)
        layout.addWidget(general)

        safety, safety_form = _card(tr("Safety"))
        self.emergency = QDoubleSpinBox()
        self.emergency.setRange(50.0, 110.0)
        self.emergency.setDecimals(0)
        self.emergency.setSuffix(" °C")
        self.emergency.setValue(settings.emergency_temperature)
        self.emergency.valueChanged.connect(self._changed)
        safety_form.addRow(tr("Emergency temperature (°C)"), self.emergency)
        note = QLabel(tr("Above this temperature every channel is forced to 100 %."))
        note.setObjectName("Faint")
        note.setWordWrap(True)
        safety.layout().addWidget(note)
        layout.addWidget(safety)

        tools, tools_form = _card(tr("Devices"))
        rescan = QPushButton(tr("Rescan devices"))
        rescan.clicked.connect(self.rescanRequested)
        tools_form.addRow(rescan)
        path = QLabel(str(config_dir()))
        path.setObjectName("Faint")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tools_form.addRow(tr("Profile"), path)
        layout.addWidget(tools)

        self._loading = False

    def _changed(self) -> None:
        if self._loading:
            return
        self.settings.poll_interval = float(self.poll.value())
        self.settings.history_seconds = int(self.history.value())
        self.settings.apply_on_start = self.apply_on_start.isChecked()
        self.settings.start_minimised = self.start_minimised.isChecked()
        self.settings.close_to_tray = self.close_to_tray.isChecked()
        self.settings.emergency_temperature = float(self.emergency.value())
        self.settings.extra["language"] = self.language.currentData()
        self.settingsChanged.emit()
