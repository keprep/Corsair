"""Rules that pick a profile on their own."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QTime

from corsair_control.core.automation import POWER_AC, POWER_BATTERY, Rule
from corsair_control.ui.i18n import tr

POWER_CHOICES = [(None, "Any"), (POWER_AC, "On mains"), (POWER_BATTERY, "On battery")]


class RuleCard(QFrame):
    """Editor for a single rule."""

    changed = pyqtSignal()
    removeRequested = pyqtSignal(object)

    def __init__(
        self, rule: Rule, profiles: Callable[[], list[str]], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMaximumWidth(960)
        self.rule = rule
        self._profiles = profiles
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.enabled = QCheckBox()
        self.enabled.setChecked(rule.enabled)
        self.enabled.stateChanged.connect(self._apply)
        header.addWidget(self.enabled)

        self.name = QLineEdit(rule.name)
        self.name.setPlaceholderText(tr("Rule name"))
        self.name.textChanged.connect(self._apply)
        header.addWidget(self.name, 2)

        header.addWidget(QLabel(tr("Profile")))
        self.profile = QComboBox()
        self.profile.currentIndexChanged.connect(self._apply)
        header.addWidget(self.profile, 1)

        header.addWidget(QLabel(tr("Priority")))
        self.priority = QSpinBox()
        self.priority.setRange(0, 99)
        self.priority.setValue(rule.priority)
        self.priority.valueChanged.connect(self._apply)
        header.addWidget(self.priority)

        remove = QPushButton("✕")
        remove.setObjectName("Danger")
        remove.setFixedWidth(34)
        remove.setToolTip(tr("Delete rule"))
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        header.addWidget(remove)
        layout.addLayout(header)

        conditions = QHBoxLayout()
        conditions.setSpacing(10)

        conditions.addWidget(QLabel(tr("Process")))
        self.process = QLineEdit(rule.process or "")
        self.process.setPlaceholderText(tr("e.g. steam"))
        self.process.textChanged.connect(self._apply)
        conditions.addWidget(self.process, 2)

        self.use_time = QCheckBox(tr("Time"))
        self.use_time.setChecked(bool(rule.time_from and rule.time_to))
        self.use_time.stateChanged.connect(self._apply)
        conditions.addWidget(self.use_time)

        self.time_from = QTimeEdit(_to_qtime(rule.time_from, 22, 0))
        self.time_from.setDisplayFormat("HH:mm")
        self.time_from.timeChanged.connect(self._apply)
        conditions.addWidget(self.time_from)

        self.time_to = QTimeEdit(_to_qtime(rule.time_to, 7, 0))
        self.time_to.setDisplayFormat("HH:mm")
        self.time_to.timeChanged.connect(self._apply)
        conditions.addWidget(self.time_to)

        conditions.addWidget(QLabel(tr("Power")))
        self.power = QComboBox()
        for value, label in POWER_CHOICES:
            self.power.addItem(tr(label), value)
        index = self.power.findData(rule.power)
        self.power.setCurrentIndex(index if index >= 0 else 0)
        self.power.currentIndexChanged.connect(self._apply)
        conditions.addWidget(self.power)

        self.use_temperature = QCheckBox(tr("Above"))
        self.use_temperature.setChecked(rule.temperature_above is not None)
        self.use_temperature.stateChanged.connect(self._apply)
        conditions.addWidget(self.use_temperature)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(30, 105)
        self.temperature.setDecimals(0)
        self.temperature.setSuffix(" °C")
        self.temperature.setValue(rule.temperature_above or 70.0)
        self.temperature.valueChanged.connect(self._apply)
        conditions.addWidget(self.temperature)
        conditions.addStretch(1)
        layout.addLayout(conditions)

        self.summary = QLabel("")
        self.summary.setObjectName("Faint")
        layout.addWidget(self.summary)

        self.refresh_profiles()
        self._loading = False
        self._update_summary()

    # ------------------------------------------------------------------
    def refresh_profiles(self) -> None:
        current = self.rule.profile
        self.profile.blockSignals(True)
        self.profile.clear()
        for name in self._profiles():
            self.profile.addItem(name, name)
        index = self.profile.findData(current)
        if index >= 0:
            self.profile.setCurrentIndex(index)
        elif self.profile.count():
            self.rule.profile = self.profile.currentData()
        self.profile.blockSignals(False)

    def _apply(self) -> None:
        if self._loading:
            return
        self.rule.enabled = self.enabled.isChecked()
        self.rule.name = self.name.text().strip() or tr("Rule")
        self.rule.profile = self.profile.currentData() or ""
        self.rule.priority = self.priority.value()
        self.rule.process = self.process.text().strip() or None
        if self.use_time.isChecked():
            self.rule.time_from = self.time_from.time().toString("HH:mm")
            self.rule.time_to = self.time_to.time().toString("HH:mm")
        else:
            self.rule.time_from = self.rule.time_to = None
        self.rule.power = self.power.currentData()
        self.rule.temperature_above = (
            float(self.temperature.value()) if self.use_temperature.isChecked() else None
        )

        self.time_from.setEnabled(self.use_time.isChecked())
        self.time_to.setEnabled(self.use_time.isChecked())
        self.temperature.setEnabled(self.use_temperature.isChecked())

        self._update_summary()
        self.changed.emit()

    def _update_summary(self) -> None:
        self.time_from.setEnabled(self.use_time.isChecked())
        self.time_to.setEnabled(self.use_time.isChecked())
        self.temperature.setEnabled(self.use_temperature.isChecked())
        if not self.rule.has_condition:
            self.summary.setText(tr("Add at least one condition, otherwise the rule never fires."))
        else:
            self.summary.setText(f"{tr('Applies when')}: {self.rule.describe()}")

    def mark_active(self, active: bool) -> None:
        self.setStyleSheet(
            "#Card { border: 1px solid #3ecf8e; }" if active else ""
        )


def _to_qtime(text: str | None, hour: int, minute: int) -> QTime:
    if text:
        parts = text.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return QTime(int(parts[0]) % 24, int(parts[1]) % 60)
    return QTime(hour, minute)


class AutomationPage(QWidget):
    """List of rules plus the master switch."""

    changed = pyqtSignal()

    def __init__(
        self,
        rules: list[Rule],
        profiles: Callable[[], list[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rules = rules
        self._profiles = profiles
        self._cards: list[RuleCard] = []

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

        header = QHBoxLayout()
        title = QLabel(tr("Automatic profiles"))
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.enabled = QCheckBox(tr("Enable automation"))
        self.enabled.stateChanged.connect(lambda: self.changed.emit())
        header.addWidget(self.enabled)
        add = QPushButton(tr("Add rule"))
        add.setObjectName("Accent")
        add.clicked.connect(self._add_rule)
        header.addWidget(add)
        self.layout_.addLayout(header)

        explain = QLabel(
            tr(
                "The highest-priority rule that matches wins. When no rule matches, "
                "the profile you last picked by hand is restored."
            )
        )
        explain.setObjectName("Faint")
        explain.setWordWrap(True)
        explain.setMaximumWidth(960)
        self.layout_.addWidget(explain)

        self.empty = QLabel(tr("No rules yet."))
        self.empty.setObjectName("Faint")
        self.layout_.addWidget(self.empty)

        self.rebuild()

    # ------------------------------------------------------------------
    def rebuild(self) -> None:
        for card in self._cards:
            self.layout_.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        for rule in self.rules:
            card = RuleCard(rule, self._profiles)
            card.changed.connect(self.changed)
            card.removeRequested.connect(self._remove)
            self.layout_.addWidget(card)
            self._cards.append(card)
        self.empty.setVisible(not self._cards)

    def refresh_profiles(self) -> None:
        for card in self._cards:
            card.refresh_profiles()

    def set_enabled_state(self, enabled: bool) -> None:
        self.enabled.blockSignals(True)
        self.enabled.setChecked(enabled)
        self.enabled.blockSignals(False)

    def highlight(self, rule_name: str | None) -> None:
        for card in self._cards:
            card.mark_active(bool(rule_name) and card.rule.name == rule_name)

    def _add_rule(self) -> None:
        names = self._profiles()
        rule = Rule(
            name=f"{tr('Rule')} {len(self.rules) + 1}",
            profile=names[0] if names else "",
            priority=len(self.rules),
        )
        self.rules.append(rule)
        self.rebuild()
        self.changed.emit()

    def _remove(self, card: RuleCard) -> None:
        if card.rule in self.rules:
            self.rules.remove(card.rule)
        self.rebuild()
        self.changed.emit()
