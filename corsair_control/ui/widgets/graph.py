"""Multi-series history graph for temperatures and fan speeds."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPaintEvent, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from corsair_control.ui.theme import PALETTE

SERIES_COLOURS = [
    PALETTE.cool,
    PALETTE.warn,
    PALETTE.good,
    PALETTE.pump,
    PALETTE.bad,
    "#54d7e0",
    "#e07ac0",
    "#b3c24a",
]


@dataclass
class Series:
    key: str
    label: str
    colour: QColor
    points: list[tuple[float, float]] = field(default_factory=list)
    visible: bool = True


class HistoryGraph(QWidget):
    """Rolling line chart. Values are appended by the owner every tick."""

    def __init__(
        self,
        *,
        window_seconds: int = 300,
        y_min: float = 0.0,
        y_max: float = 100.0,
        unit: str = "°C",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.window_seconds = window_seconds
        self.y_min = y_min
        self.y_max = y_max
        self.unit = unit
        self._series: dict[str, Series] = {}
        self._order: list[str] = []
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------
    def ensure_series(self, key: str, label: str) -> Series:
        series = self._series.get(key)
        if series is None:
            colour = QColor(SERIES_COLOURS[len(self._series) % len(SERIES_COLOURS)])
            series = Series(key=key, label=label, colour=colour)
            self._series[key] = series
            self._order.append(key)
        else:
            series.label = label
        return series

    def set_series_keys(self, keys: list[tuple[str, str]]) -> None:
        """Restrict the graph to the given (key, label) pairs, keeping data."""
        wanted = {k for k, _ in keys}
        for key in list(self._series):
            if key not in wanted:
                self._series.pop(key, None)
                if key in self._order:
                    self._order.remove(key)
        for key, label in keys:
            self.ensure_series(key, label)
        self.update()

    def append(self, values: dict[str, float]) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        for key, series in self._series.items():
            value = values.get(key)
            if value is not None:
                series.points.append((now, float(value)))
            while series.points and series.points[0][0] < cutoff:
                series.points.pop(0)
        self.update()

    def clear(self) -> None:
        self._series.clear()
        self._order.clear()
        self.update()

    # ------------------------------------------------------------------
    def _plot_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(42, 12, -12, -34)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(PALETTE.surface_alt)))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        painter.setFont(font)

        steps = 4
        for i in range(steps + 1):
            ratio = i / steps
            y = rect.bottom() - ratio * rect.height()
            painter.setPen(QPen(QColor(PALETTE.border)))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QPen(QColor(PALETTE.text_faint)))
            value = self.y_min + ratio * (self.y_max - self.y_min)
            painter.drawText(
                QRectF(0, y - 9, rect.left() - 6, 18),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{value:.0f}",
            )

        now = time.monotonic()
        span = float(self.window_seconds)
        y_span = max(1e-6, self.y_max - self.y_min)

        for key in self._order:
            series = self._series.get(key)
            if series is None or not series.visible or len(series.points) < 2:
                continue
            path = QPainterPath()
            started = False
            for timestamp, value in series.points:
                age = now - timestamp
                if age > span:
                    continue
                x = rect.right() - (age / span) * rect.width()
                y = rect.bottom() - (value - self.y_min) / y_span * rect.height()
                y = max(rect.top(), min(rect.bottom(), y))
                point = QPointF(x, y)
                if started:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    started = True
            if not started:
                continue
            pen = QPen(series.colour)
            pen.setWidthF(1.8)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        self._paint_legend(painter, rect)
        painter.end()

    def _paint_legend(self, painter: QPainter, rect: QRectF) -> None:
        x = rect.left()
        y = rect.bottom() + 8
        metrics = painter.fontMetrics()
        for key in self._order:
            series = self._series.get(key)
            if series is None:
                continue
            label = series.label
            if series.points:
                label = f"{label}  {series.points[-1][1]:.0f} {self.unit}"
            width = metrics.horizontalAdvance(label) + 22
            if x + width > rect.right():
                break
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(series.colour))
            painter.drawEllipse(QPointF(x + 4, y + 8), 3.5, 3.5)
            painter.setPen(QPen(QColor(PALETTE.text_dim)))
            painter.drawText(
                QRectF(x + 12, y, width - 12, 17),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                label,
            )
            x += width
