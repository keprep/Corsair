"""Multi-series history graph for temperatures and fan speeds."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from corsair_control.ui.i18n import tr
from corsair_control.ui.theme import PALETTE, mix

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
    """Rolling line chart. Values are appended by the owner every tick.

    Hovering shows a crosshair with the value of every series at that moment,
    which is the whole point of keeping a history: spotting what the fans did
    while something else was heating up.
    """

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
        self._hover_x: float | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(170)
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
        return QRectF(self.rect()).adjusted(44, 12, -14, -46)

    def mouseMoveEvent(self, event) -> None:
        rect = self._plot_rect()
        x = event.position().x()
        self._hover_x = x if rect.left() <= x <= rect.right() else None
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover_x = None
        self.update()

    def _to_pixel(self, rect: QRectF, now: float, timestamp: float, value: float) -> QPointF:
        span = float(self.window_seconds)
        y_span = max(1e-6, self.y_max - self.y_min)
        x = rect.right() - ((now - timestamp) / span) * rect.width()
        y = rect.bottom() - (value - self.y_min) / y_span * rect.height()
        return QPointF(x, max(rect.top(), min(rect.bottom(), y)))

    # ------------------------------------------------------------------
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

        self._paint_grid(painter, rect)

        now = time.monotonic()
        for key in self._order:
            series = self._series.get(key)
            if series is not None and series.visible and len(series.points) >= 2:
                self._paint_series(painter, rect, series, now)

        self._paint_crosshair(painter, rect, now)
        self._paint_legend(painter, rect)
        painter.end()

    def _paint_grid(self, painter: QPainter, rect: QRectF) -> None:
        steps = 4
        for index in range(steps + 1):
            ratio = index / steps
            y = rect.bottom() - ratio * rect.height()
            painter.setPen(QPen(QColor(PALETTE.border)))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QPen(QColor(PALETTE.text_faint)))
            value = self.y_min + ratio * (self.y_max - self.y_min)
            painter.drawText(
                QRectF(0, y - 9, rect.left() - 8, 18),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{value:.0f}",
            )

        minutes = self.window_seconds / 60.0
        for fraction, label in ((0.0, f"-{minutes:.0f} min"), (0.5, ""), (1.0, tr("now"))):
            x = rect.left() + fraction * rect.width()
            if fraction not in (0.0, 1.0):
                pen = QPen(QColor(PALETTE.border))
                pen.setStyle(Qt.PenStyle.DotLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if not label:
                continue
            painter.setPen(QPen(QColor(PALETTE.text_faint)))
            left = fraction == 0.0
            box = QRectF(x if left else x - 84, rect.bottom() + 3, 84, 14)
            align = Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag.AlignRight
            )
            painter.drawText(box, int(align), label)

    def _paint_series(
        self, painter: QPainter, rect: QRectF, series: Series, now: float
    ) -> None:
        span = float(self.window_seconds)
        path = QPainterPath()
        started = False
        last: QPointF | None = None
        for timestamp, value in series.points:
            if now - timestamp > span:
                continue
            point = self._to_pixel(rect, now, timestamp, value)
            if started:
                path.lineTo(point)
            else:
                path.moveTo(point)
                started = True
            last = point
        if not started or last is None:
            return

        fill = QPainterPath(path)
        fill.lineTo(QPointF(last.x(), rect.bottom()))
        fill.lineTo(QPointF(path.elementAt(0).x, rect.bottom()))
        fill.closeSubpath()

        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        top = QColor(series.colour)
        top.setAlpha(55)
        bottom = QColor(series.colour)
        bottom.setAlpha(0)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(fill)

        pen = QPen(series.colour)
        pen.setWidthF(1.9)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(series.colour))
        painter.drawEllipse(last, 3.0, 3.0)

    def _value_at(self, series: Series, now: float, timestamp: float) -> float | None:
        best: tuple[float, float] | None = None
        for point_time, value in series.points:
            distance = abs(point_time - timestamp)
            if best is None or distance < best[0]:
                best = (distance, value)
        if best is None or best[0] > self.window_seconds / 20:
            return None
        return best[1]

    def _paint_crosshair(self, painter: QPainter, rect: QRectF, now: float) -> None:
        if self._hover_x is None:
            return
        span = float(self.window_seconds)
        timestamp = now - (rect.right() - self._hover_x) / rect.width() * span

        pen = QPen(QColor(PALETTE.text_faint))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(self._hover_x, rect.top()), QPointF(self._hover_x, rect.bottom()))

        rows: list[tuple[QColor, str]] = []
        for key in self._order:
            series = self._series.get(key)
            if series is None or not series.points:
                continue
            value = self._value_at(series, now, timestamp)
            if value is None:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(series.colour))
            painter.drawEllipse(self._to_pixel(rect, now, timestamp, value), 3.5, 3.5)
            rows.append((series.colour, f"{series.label} {value:.0f} {self.unit}"))
        if not rows:
            return

        metrics = painter.fontMetrics()
        width = max(metrics.horizontalAdvance(text) for _, text in rows) + 26
        height = len(rows) * (metrics.height() + 2) + 10
        left = self._hover_x + 12
        if left + width > rect.right():
            left = self._hover_x - width - 12
        box = QRectF(left, rect.top() + 8, width, height)

        painter.setPen(QPen(QColor(PALETTE.border)))
        painter.setBrush(QBrush(mix(PALETTE.surface, "#000000", 0.25)))
        painter.drawRoundedRect(box, 8, 8)

        y = box.top() + 5
        for colour, text in rows:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(colour))
            painter.drawEllipse(QPointF(box.left() + 10, y + metrics.height() / 2), 3.0, 3.0)
            painter.setPen(QPen(QColor(PALETTE.text)))
            painter.drawText(
                QRectF(box.left() + 18, y, box.width() - 22, metrics.height()),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
            y += metrics.height() + 2

    def _paint_legend(self, painter: QPainter, rect: QRectF) -> None:
        x = rect.left()
        y = rect.bottom() + 21
        metrics = painter.fontMetrics()
        for key in self._order:
            series = self._series.get(key)
            if series is None:
                continue
            label = series.label
            if series.points:
                label = f"{label}  {series.points[-1][1]:.0f} {self.unit}"
            width = metrics.horizontalAdvance(label) + 26
            if x + width > rect.right():
                break
            chip = QRectF(x, y, width - 8, metrics.height() + 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(mix(PALETTE.surface_hover, series.colour, 0.12)))
            painter.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)

            painter.setBrush(QBrush(series.colour))
            painter.drawEllipse(QPointF(chip.left() + 9, chip.center().y()), 3.5, 3.5)
            painter.setPen(QPen(QColor(PALETTE.text_dim)))
            painter.drawText(
                chip.adjusted(17, 0, -6, 0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                label,
            )
            x += width
