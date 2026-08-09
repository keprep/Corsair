"""Runs a fan calibration sweep with progress and a cancel button."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.calibration import CalibrationCancelled, ChannelCalibration
from corsair_control.core.engine import ControlEngine
from corsair_control.ui.i18n import tr


class _Worker(QObject):
    progressed = pyqtSignal(float, str)
    finished = pyqtSignal(object, str)

    def __init__(
        self, engine: ControlEngine, device_key: str, channel_id: str, cancel: threading.Event
    ) -> None:
        super().__init__()
        self.engine = engine
        self.device_key = device_key
        self.channel_id = channel_id
        self.cancel = cancel

    def run(self) -> None:
        try:
            result = self.engine.calibrate(
                self.device_key,
                self.channel_id,
                progress=lambda fraction, message: self.progressed.emit(fraction, message),
                cancel=self.cancel,
            )
        except CalibrationCancelled:
            self.finished.emit(None, tr("Cancelled"))
        except Exception as exc:  # noqa: BLE001 - surfaced in the dialog
            self.finished.emit(None, str(exc))
        else:
            self.finished.emit(result, "")


class CalibrationDialog(QDialog):
    """Modal progress window around :meth:`ControlEngine.calibrate`."""

    def __init__(
        self,
        engine: ControlEngine,
        device_key: str,
        channel_id: str,
        title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{tr('Calibrate fan')} · {title}")
        self.setModal(True)
        self.setMinimumWidth(430)
        self.result_data: ChannelCalibration | None = None

        self._cancel = threading.Event()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        heading = QLabel(tr("Calibrate fan"))
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)

        explain = QLabel(
            tr(
                "The channel is stepped from 100 % down to 0 % and back up. "
                "This takes a minute or two and the fan will get loud, then stop."
            )
        )
        explain.setObjectName("Faint")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        layout.addWidget(self.bar)

        self.status = QLabel("")
        self.status.setObjectName("Faint")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton(tr("Cancel"))
        self.cancel_button.clicked.connect(self._request_cancel)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self._thread = QThread(self)
        self._worker = _Worker(engine, device_key, channel_id, self._cancel)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    # ------------------------------------------------------------------
    def _on_progress(self, fraction: float, message: str) -> None:
        self.bar.setValue(int(fraction * 100))
        self.status.setText(message)

    def _on_finished(self, result: object, error: str) -> None:
        self._thread.quit()
        self._thread.wait(3000)
        if isinstance(result, ChannelCalibration):
            self.result_data = result
            self.accept()
            return
        self.status.setText(error or tr("Cancelled"))
        self.cancel_button.setText(tr("Close"))
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.reject)

    def _request_cancel(self) -> None:
        self._cancel.set()
        self.cancel_button.setEnabled(False)
        self.status.setText(tr("Stopping…"))

    def closeEvent(self, event) -> None:
        self._cancel.set()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        # Escape must not drop the dialog while the hardware is mid-sweep.
        if event.key() == Qt.Key.Key_Escape and self._thread.isRunning():
            self._request_cancel()
            return
        super().keyPressEvent(event)
