"""Circular gauge used for temperatures, RPM and duty readouts."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from corsair_control.ui.theme import PALETTE, temperature_colour

START_ANGLE = 225 * 16
SPAN_ANGLE = -270 * 16


class Gauge(QWidget):
    """A 270° arc with the value in the middle."""

    def __init__(
        self,
        title: str = "",
        unit: str = "°C",
        minimum: float = 0.0,
        maximum: float = 100.0,
        *,
        colour_by_temperature: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.minimum = minimum
        self.maximum = maximum
        self.colour_by_temperature = colour_by_temperature
        self._value: float | None = None
        self._subtitle = ""
        self._colour = QColor(PALETTE.accent)
        self.setMinimumSize(112, 118)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def set_value(self, value: float | None, subtitle: str = "") -> None:
        if value == self._value and subtitle == self._subtitle:
            return
        self._value = value
        self._subtitle = subtitle
        self.update()

    def set_colour(self, colour: QColor | str) -> None:
        self._colour = QColor(colour)
        self.update()

    def _ratio(self) -> float:
        if self._value is None:
            return 0.0
        span = self.maximum - self.minimum
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self._value - self.minimum) / span))

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = min(self.width(), self.height() - 18)
        thickness = max(7.0, size * 0.085)
        rect = QRectF(
            (self.width() - size) / 2 + thickness / 2,
            thickness / 2,
            size - thickness,
            size - thickness,
        )

        track = QPen(QColor(PALETTE.surface_hover), thickness)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, START_ANGLE, SPAN_ANGLE)

        colour = (
            temperature_colour(self._value) if self.colour_by_temperature else self._colour
        )
        if self._value is not None:
            arc = QPen(colour, thickness)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc)
            painter.drawArc(rect, START_ANGLE, int(SPAN_ANGLE * self._ratio()))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        value_font = QFont(self.font())
        value_font.setPointSizeF(max(12.0, size * 0.20))
        value_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(value_font)
        painter.setPen(QPen(QColor(PALETTE.text)))
        text = "—" if self._value is None else f"{self._value:.0f}"
        painter.drawText(
            rect.adjusted(0, -size * 0.06, 0, -size * 0.06),
            int(Qt.AlignmentFlag.AlignCenter),
            text,
        )

        small = QFont(self.font())
        small.setPointSizeF(max(7.0, size * 0.085))
        painter.setFont(small)
        painter.setPen(QPen(QColor(PALETTE.text_dim)))
        painter.drawText(
            rect.adjusted(0, size * 0.20, 0, size * 0.20),
            int(Qt.AlignmentFlag.AlignCenter),
            self._subtitle or self.unit,
        )

        if self.title:
            painter.setPen(QPen(QColor(PALETTE.text_dim)))
            painter.drawText(
                QRectF(0, self.height() - 17, self.width(), 16),
                int(Qt.AlignmentFlag.AlignCenter),
                self.title,
            )
        painter.end()


class Bar(QWidget):
    """A slim horizontal bar - the compact sibling of :class:`Gauge`."""

    def __init__(self, maximum: float = 100.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.maximum = maximum
        self._value = 0.0
        self._colour = QColor(PALETTE.accent)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: float | None) -> None:
        value = 0.0 if value is None else value
        if abs(value - self._value) < 0.05:
            return
        self._value = value
        self.update()

    def set_colour(self, colour: QColor | str) -> None:
        self._colour = QColor(colour)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2

        painter.setBrush(QBrush(QColor(PALETTE.surface_hover)))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)

        ratio = max(0.0, min(1.0, self._value / self.maximum if self.maximum else 0.0))
        if ratio > 0:
            painter.setBrush(QBrush(self._colour))
            painter.drawRoundedRect(
                QRectF(0, 0, max(self.height(), self.width() * ratio), self.height()),
                radius,
                radius,
            )
        painter.end()
