"""Bind an unrecognised Corsair device to a driver, from the UI."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.config import Settings
from corsair_control.core.experimental import PROTOCOLS, Binding, probe
from corsair_control.ui.i18n import tr
from corsair_control.ui.theme import PALETTE


class _Probe(QObject):
    done = pyqtSignal(bool, str)

    def __init__(self, binding: Binding) -> None:
        super().__init__()
        self.binding = binding

    def run(self) -> None:
        backend, outcome = probe(self.binding)
        if backend is not None:
            try:
                backend.disconnect()
            except Exception:  # pragma: no cover - best effort
                pass
        self.done.emit(outcome.ok, outcome.describe())


class BindingDialog(QDialog):
    """Pick a device, pick a protocol, try it, keep it if it answered."""

    def __init__(
        self,
        unclaimed: list[tuple[int, int, str]],
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Unsupported devices"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.settings = settings
        self.saved = False
        self._thread: QThread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        heading = QLabel(tr("Bind a device by hand"))
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)

        explain = QLabel(
            tr(
                "Corsair sometimes ships a new revision under a new USB ID, and "
                "liquidctl then does not recognise it even though the protocol is "
                "unchanged. Here you can tell the application to treat such a device "
                "as a model it does know."
            )
        )
        explain.setObjectName("Faint")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        warning = QLabel(
            tr(
                "Testing writes to the device. Only do this for cooling hardware - "
                "never point it at a keyboard, mouse or headset."
            )
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            f"color: {PALETTE.warn}; border: 1px solid {PALETTE.warn};"
            "border-radius: 8px; padding: 8px 10px;"
        )
        layout.addWidget(warning)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel(tr("Device")))
        self.device_box = QComboBox()
        for vendor, product, name in unclaimed:
            self.device_box.addItem(f"{vendor:04x}:{product:04x} · {name}", (vendor, product))
        row.addWidget(self.device_box, 2)

        row.addWidget(QLabel(tr("Treat as")))
        self.protocol_box = QComboBox()
        for protocol in PROTOCOLS.values():
            self.protocol_box.addItem(protocol.label, protocol.key)
        row.addWidget(self.protocol_box, 2)
        layout.addLayout(row)

        self.note = QLabel("")
        self.note.setObjectName("Faint")
        self.note.setWordWrap(True)
        self.protocol_box.currentIndexChanged.connect(self._show_note)
        layout.addWidget(self.note)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.test_button = QPushButton(tr("Test"))
        self.test_button.clicked.connect(self._test)
        buttons.addWidget(self.test_button)

        self.save_button = QPushButton(tr("Keep binding"))
        self.save_button.setObjectName("Accent")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)

        close = QPushButton(tr("Close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        if not unclaimed:
            self.device_box.setEnabled(False)
            self.test_button.setEnabled(False)
            self.result.setText(tr("Every connected Corsair device already has a driver."))

        self._show_note()

    # ------------------------------------------------------------------
    def _binding(self) -> Binding | None:
        data = self.device_box.currentData()
        if not data:
            return None
        vendor, product = data
        return Binding(vendor, product, self.protocol_box.currentData())

    def _show_note(self) -> None:
        protocol = PROTOCOLS.get(self.protocol_box.currentData() or "")
        self.note.setText(f"{tr('Matches')}: {protocol.note}" if protocol and protocol.note else "")

    def _test(self) -> None:
        binding = self._binding()
        if binding is None:
            return
        self.test_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.result.setText(tr("Testing…"))
        self.result.setStyleSheet(f"color: {PALETTE.text_dim};")

        worker = _Probe(binding)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._finished)
        worker.done.connect(thread.quit)
        self._worker = worker
        self._thread = thread
        thread.start()

    def _finished(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.save_button.setEnabled(ok)
        if ok:
            self.result.setText(f"{tr('The device answered')}: {message}")
            self.result.setStyleSheet(f"color: {PALETTE.good};")
        else:
            self.result.setText(message)
            self.result.setStyleSheet(f"color: {PALETTE.bad};")

    def _save(self) -> None:
        binding = self._binding()
        if binding is None:
            return
        kept = [
            b for b in self.settings.experimental_bindings
            if not str(b).startswith(binding.usb_id)
        ]
        kept.append(str(binding))
        self.settings.experimental_bindings = kept
        self.settings.save()
        self.saved = True
        self.accept()

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._thread and self._thread.isRunning():
            return
        super().keyPressEvent(event)
