"""Settings page."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.alarms import AlarmSettings
from corsair_control.core.config import Settings, config_dir, state_dir
from corsair_control.ui.i18n import tr
from corsair_control.ui.theme import ACCENTS


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


class AccentPicker(QWidget):
    """A row of colour dots; the active one gets a ring."""

    accentPicked = pyqtSignal(str)

    def __init__(self, current: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._buttons: dict[str, QPushButton] = {}

        for name, colour in ACCENTS.items():
            button = QPushButton()
            button.setFixedSize(26, 26)
            button.setToolTip(name)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, c=colour: self._pick(c))
            self._buttons[colour] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self._paint(current)

    def _pick(self, colour: str) -> None:
        self._paint(colour)
        self.accentPicked.emit(colour)

    def _paint(self, current: str) -> None:
        for colour, button in self._buttons.items():
            ring = "#ffffff" if colour.lower() == current.lower() else "transparent"
            button.setStyleSheet(
                f"QPushButton {{ background: {colour}; border-radius: 13px;"
                f" border: 2px solid {ring}; }}"
            )


class SettingsPage(QWidget):
    settingsChanged = pyqtSignal()
    accentChanged = pyqtSignal(str)
    rescanRequested = pyqtSignal()
    alarmsChanged = pyqtSignal()
    exportRequested = pyqtSignal()

    def __init__(
        self, settings: Settings, alarms: AlarmSettings, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.alarms = alarms
        self._loading = True

        # The page outgrew a single screen, so it scrolls like the others.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)

        layout = QVBoxLayout(body)
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

        appearance, appearance_form = _card(tr("Appearance"))
        self.accent = AccentPicker(settings.accent)
        self.accent.accentPicked.connect(self._on_accent)
        appearance_form.addRow(tr("Accent colour"), self.accent)
        layout.addWidget(appearance)

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

        alarms_card, alarms_form = _card(tr("Alarms"))
        self.alarms_enabled = QCheckBox()
        self.alarms_enabled.setChecked(alarms.enabled)
        self.alarms_enabled.stateChanged.connect(self._on_alarms)
        alarms_form.addRow(tr("Enable alarms"), self.alarms_enabled)

        self.pump_minimum = QSpinBox()
        self.pump_minimum.setRange(0, 5000)
        self.pump_minimum.setSingleStep(50)
        self.pump_minimum.setSuffix(" rpm")
        self.pump_minimum.setValue(int(alarms.pump_minimum_rpm))
        self.pump_minimum.valueChanged.connect(self._on_alarms)
        alarms_form.addRow(tr("Warn below pump speed"), self.pump_minimum)

        self.stall_duty = QSpinBox()
        self.stall_duty.setRange(0, 100)
        self.stall_duty.setSuffix(" %")
        self.stall_duty.setValue(int(alarms.fan_stall_duty))
        self.stall_duty.valueChanged.connect(self._on_alarms)
        alarms_form.addRow(tr("Fan counts as stalled above"), self.stall_duty)

        self.warn_temperature = QDoubleSpinBox()
        self.warn_temperature.setRange(40, 110)
        self.warn_temperature.setDecimals(0)
        self.warn_temperature.setSuffix(" °C")
        self.warn_temperature.setValue(alarms.temperature_warning)
        self.warn_temperature.valueChanged.connect(self._on_alarms)
        alarms_form.addRow(tr("Warn above temperature"), self.warn_temperature)

        alarm_note = QLabel(
            tr("A condition must hold for a few seconds before it raises an alarm.")
        )
        alarm_note.setObjectName("Faint")
        alarm_note.setWordWrap(True)
        alarms_card.layout().addWidget(alarm_note)
        layout.addWidget(alarms_card)

        history_card, history_form = _card(tr("Recording"))
        self.record = QCheckBox()
        self.record.setChecked(settings.record_history)
        self.record.stateChanged.connect(self._changed)
        history_form.addRow(tr("Record measurements to CSV"), self.record)

        self.retention = QSpinBox()
        self.retention.setRange(1, 365)
        self.retention.setSuffix(" " + tr("days"))
        self.retention.setValue(settings.history_retention_days)
        self.retention.valueChanged.connect(self._changed)
        history_form.addRow(tr("Keep recordings for"), self.retention)

        export = QPushButton(tr("Export history as CSV…"))
        export.clicked.connect(self.exportRequested)
        history_form.addRow(export)

        record_path = QLabel(str(state_dir()))
        record_path.setObjectName("Faint")
        record_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        history_form.addRow(tr("Folder"), record_path)
        layout.addWidget(history_card)

        tools, tools_form = _card(tr("Devices"))
        self.mainboard = QCheckBox()
        self.mainboard.setChecked(settings.control_mainboard_fans)
        self.mainboard.stateChanged.connect(self._changed)
        tools_form.addRow(tr("Also control mainboard fans (hwmon)"), self.mainboard)

        rescan = QPushButton(tr("Rescan devices"))
        rescan.clicked.connect(self.rescanRequested)
        tools_form.addRow(rescan)
        path = QLabel(str(config_dir()))
        path.setObjectName("Faint")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tools_form.addRow(tr("Profile"), path)
        hwmon_note = QLabel(
            tr("Mainboard headers need root - use the corsair-controld service for them.")
        )
        hwmon_note.setObjectName("Faint")
        hwmon_note.setWordWrap(True)
        tools.layout().addWidget(hwmon_note)
        layout.addWidget(tools)

        self._loading = False

    def _on_alarms(self) -> None:
        if self._loading:
            return
        self.alarms.enabled = self.alarms_enabled.isChecked()
        self.alarms.pump_minimum_rpm = float(self.pump_minimum.value())
        self.alarms.fan_stall_duty = float(self.stall_duty.value())
        self.alarms.temperature_warning = float(self.warn_temperature.value())
        self.alarmsChanged.emit()

    def _on_accent(self, colour: str) -> None:
        # The window owns persistence so that the setting is stored no matter
        # who triggers the change.
        self.accentChanged.emit(colour)

    def _changed(self) -> None:
        if self._loading:
            return
        self.settings.poll_interval = float(self.poll.value())
        self.settings.history_seconds = int(self.history.value())
        self.settings.apply_on_start = self.apply_on_start.isChecked()
        self.settings.start_minimised = self.start_minimised.isChecked()
        self.settings.close_to_tray = self.close_to_tray.isChecked()
        self.settings.emergency_temperature = float(self.emergency.value())
        self.settings.record_history = self.record.isChecked()
        self.settings.history_retention_days = int(self.retention.value())
        self.settings.control_mainboard_fans = self.mainboard.isChecked()
        self.settings.extra["language"] = self.language.currentData()
        self.settingsChanged.emit()
