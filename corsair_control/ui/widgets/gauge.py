"""Circular gauge and slim bar used for temperature, RPM and duty readouts.

Both widgets ease towards a new value instead of jumping to it. A dashboard
full of numbers that snap around is hard to read; 400 ms of easing makes the
same data feel calm without hiding anything.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from corsair_control.ui.icons import glyph_pixmap
from corsair_control.ui.theme import PALETTE, mix, temperature_colour

START_DEGREES = 225.0
SPAN_DEGREES = -270.0
START_ANGLE = int(START_DEGREES * 16)
SPAN_ANGLE = int(SPAN_DEGREES * 16)
ANIMATION_MS = 420


class Gauge(QWidget):
    """A 270° arc with the value in the middle and tick marks around it."""

    def __init__(
        self,
        title: str = "",
        unit: str = "°C",
        minimum: float = 0.0,
        maximum: float = 100.0,
        *,
        colour_by_temperature: bool = True,
        glyph: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.minimum = minimum
        self.maximum = maximum
        self.colour_by_temperature = colour_by_temperature
        self.glyph = glyph
        self._value: float | None = None
        self._display = 0.0
        self._trend = 0
        self._subtitle = ""
        self._colour = QColor(PALETTE.accent)

        self._animation = QPropertyAnimation(self, b"display", self)
        self._animation.setDuration(ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setMinimumSize(112, 124)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    # ------------------------------------------------------------------
    def _get_display(self) -> float:
        return self._display

    def _set_display(self, value: float) -> None:
        self._display = value
        self.update()

    display = pyqtProperty(float, _get_display, _set_display)

    # ------------------------------------------------------------------
    def set_value(self, value: float | None, subtitle: str = "") -> None:
        if value == self._value and subtitle == self._subtitle:
            return
        if value is not None and self._value is not None:
            delta = value - self._value
            self._trend = 1 if delta > 0.4 else -1 if delta < -0.4 else 0
        self._value = value
        self._subtitle = subtitle

        if value is None:
            self._animation.stop()
            self.update()
            return
        self._animation.stop()
        self._animation.setStartValue(self._display)
        self._animation.setEndValue(float(value))
        self._animation.start()

    def set_colour(self, colour: QColor | str) -> None:
        self._colour = QColor(colour)
        self.update()

    def _ratio(self) -> float:
        span = self.maximum - self.minimum
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self._display - self.minimum) / span))

    def _arc_colour(self) -> QColor:
        if self.colour_by_temperature:
            return temperature_colour(self._display if self._value is not None else None)
        return self._colour

    # ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        label_height = 18 if self.title else 4
        size = min(self.width(), self.height() - label_height)
        thickness = max(7.0, size * 0.085)
        rect = QRectF(
            (self.width() - size) / 2 + thickness / 2,
            thickness / 2,
            size - thickness,
            size - thickness,
        )
        colour = self._arc_colour()

        self._paint_ticks(painter, rect, thickness, colour)

        track = QPen(QColor(PALETTE.surface_hover), thickness)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, START_ANGLE, SPAN_ANGLE)

        if self._value is not None:
            gradient = QConicalGradient(rect.center(), START_DEGREES)
            gradient.setColorAt(0.0, mix(colour, "#ffffff", 0.25))
            gradient.setColorAt(0.55, colour)
            gradient.setColorAt(1.0, mix(colour, PALETTE.bg, 0.35))
            arc = QPen(QBrush(gradient), thickness)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc)
            painter.drawArc(rect, START_ANGLE, int(SPAN_ANGLE * self._ratio()))

        self._paint_centre(painter, rect, size, colour)

        if self.title:
            painter.setPen(QPen(QColor(PALETTE.text_dim)))
            small = QFont(self.font())
            small.setPointSizeF(max(7.5, self.font().pointSizeF() - 1.0))
            painter.setFont(small)
            painter.drawText(
                QRectF(0, self.height() - 17, self.width(), 16),
                int(Qt.AlignmentFlag.AlignCenter),
                self.title,
            )
        painter.end()

    def _paint_ticks(
        self, painter: QPainter, rect: QRectF, thickness: float, colour: QColor
    ) -> None:
        # Inside the ring: outside would be clipped by the widget bounds.
        centre = rect.center()
        outer = rect.width() / 2 - thickness * 0.78
        inner = outer - max(3.0, thickness * 0.42)
        painter.save()
        painter.translate(centre)
        for index in range(11):
            fraction = index / 10.0
            painter.save()
            painter.rotate(-START_DEGREES + 90 - SPAN_DEGREES * fraction)
            lit = self._value is not None and fraction <= self._ratio() + 1e-6
            pen = QPen(colour if lit else QColor(PALETTE.border))
            pen.setWidthF(1.6 if index % 5 else 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(0, -inner), QPointF(0, -outer))
            painter.restore()
        painter.restore()

    def _paint_centre(
        self, painter: QPainter, rect: QRectF, size: float, colour: QColor
    ) -> None:
        value_font = QFont(self.font())
        value_font.setPointSizeF(max(12.0, size * 0.21))
        value_font.setWeight(QFont.Weight.Bold)
        painter.setFont(value_font)
        painter.setPen(QPen(QColor(PALETTE.text)))
        text = "—" if self._value is None else f"{self._value:.0f}"
        painter.drawText(
            rect.adjusted(0, -size * 0.05, 0, -size * 0.05),
            int(Qt.AlignmentFlag.AlignCenter),
            text,
        )

        small = QFont(self.font())
        small.setPointSizeF(max(7.0, size * 0.085))
        painter.setFont(small)
        painter.setPen(QPen(QColor(PALETTE.text_dim)))
        subtitle = self._subtitle or self.unit
        if self._trend and self._value is not None:
            subtitle = f"{'▲' if self._trend > 0 else '▼'} {subtitle}"
            painter.setPen(QPen(colour))
        sub_rect = rect.adjusted(0, size * 0.20, 0, size * 0.20)
        painter.drawText(sub_rect, int(Qt.AlignmentFlag.AlignCenter), subtitle)

        if self.glyph:
            pixmap = glyph_pixmap(self.glyph, QColor(PALETTE.text_faint), max(12, int(size * 0.15)))
            painter.drawPixmap(
                QPointF(
                    rect.center().x() - pixmap.width() / 2,
                    rect.top() + size * 0.12,
                ),
                pixmap,
            )


class Bar(QWidget):
    """A slim horizontal bar - the compact sibling of :class:`Gauge`."""

    def __init__(self, maximum: float = 100.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.maximum = maximum
        self._value = 0.0
        self._display = 0.0
        self._colour = QColor(PALETTE.accent)
        self._animation = QPropertyAnimation(self, b"display", self)
        self._animation.setDuration(ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFixedHeight(7)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _get_display(self) -> float:
        return self._display

    def _set_display(self, value: float) -> None:
        self._display = value
        self.update()

    display = pyqtProperty(float, _get_display, _set_display)

    def set_value(self, value: float | None) -> None:
        value = 0.0 if value is None else float(value)
        if abs(value - self._value) < 0.05:
            return
        self._value = value
        self._animation.stop()
        self._animation.setStartValue(self._display)
        self._animation.setEndValue(value)
        self._animation.start()

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

        ratio = max(0.0, min(1.0, self._display / self.maximum if self.maximum else 0.0))
        if ratio > 0:
            width = max(self.height(), self.width() * ratio)
            gradient = QLinearGradient(0, 0, width, 0)
            gradient.setColorAt(0.0, mix(self._colour, PALETTE.bg, 0.35))
            gradient.setColorAt(1.0, self._colour)
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(QRectF(0, 0, width, self.height()), radius, radius)
        painter.end()
