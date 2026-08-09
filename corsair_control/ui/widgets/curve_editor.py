"""Interactive fan-curve editor.

Points are dragged directly on the plot. The live operating point is drawn on
top of the curve so the effect of an edit is visible while the fans are
running.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from corsair_control.core.curve import FanCurve
from corsair_control.ui.theme import PALETTE, temperature_colour

HIT_RADIUS = 11.0


class CurveWidget(QWidget):
    """A plot of duty (%) over temperature (°C) with draggable points."""

    curveChanged = pyqtSignal()
    pointCountChanged = pyqtSignal(int)

    def __init__(
        self,
        curve: FanCurve | None = None,
        *,
        interactive: bool = True,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._curve = curve or FanCurve()
        self.interactive = interactive
        self.compact = compact
        self._accent = QColor(PALETTE.accent)
        self._drag_index: int | None = None
        self._hover_index: int | None = None
        self._selected: int | None = None
        self._live_temp: float | None = None
        self._live_duty: float | None = None
        self._temp_lo = 20.0
        self._temp_hi = 100.0

        self.setMouseTracking(interactive)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if interactive else Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(90 if compact else 260)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred if compact else QSizePolicy.Policy.Expanding,
        )
        if interactive:
            self.setCursor(Qt.CursorShape.CrossCursor)

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------
    @property
    def curve(self) -> FanCurve:
        return self._curve

    def set_curve(self, curve: FanCurve) -> None:
        self._curve = curve
        self._drag_index = None
        self._selected = None
        self.update()
        self.pointCountChanged.emit(len(curve.points))

    def set_accent(self, colour: QColor | str) -> None:
        self._accent = QColor(colour)
        self.update()

    def set_live(self, temp: float | None, duty: float | None) -> None:
        if (temp, duty) == (self._live_temp, self._live_duty):
            return
        self._live_temp = temp
        self._live_duty = duty
        self.update()

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def _plot_rect(self) -> QRectF:
        if self.compact:
            return QRectF(self.rect()).adjusted(4, 4, -4, -4)
        return QRectF(self.rect()).adjusted(44, 14, -14, -30)

    def _bounds(self) -> tuple[float, float]:
        lo = min(self._temp_lo, self._curve.points[0].temp) if self._curve.points else self._temp_lo
        if self._live_temp is not None:
            lo = min(lo, self._live_temp - 2)
        return max(0.0, lo - (lo % 5)), self._temp_hi

    def _to_pixel(self, temp: float, duty: float) -> QPointF:
        rect = self._plot_rect()
        lo, hi = self._bounds()
        x = rect.left() + (temp - lo) / max(1e-6, hi - lo) * rect.width()
        y = rect.bottom() - duty / 100.0 * rect.height()
        return QPointF(x, y)

    def _to_value(self, pos: QPointF) -> tuple[float, float]:
        rect = self._plot_rect()
        lo, hi = self._bounds()
        temp = lo + (pos.x() - rect.left()) / max(1.0, rect.width()) * (hi - lo)
        duty = (rect.bottom() - pos.y()) / max(1.0, rect.height()) * 100.0
        return (
            round(max(lo, min(hi, temp))),
            round(max(0.0, min(100.0, duty))),
        )

    def _point_at(self, pos: QPointF) -> int | None:
        best: tuple[float, int] | None = None
        for index, point in enumerate(self._curve.points):
            pixel = self._to_pixel(point.temp, point.duty)
            distance = (pixel - pos).manhattanLength()
            if distance <= HIT_RADIUS * 2 and (best is None or distance < best[0]):
                best = (distance, index)
        return best[1] if best else None

    # ------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.interactive:
            return
        pos = event.position()
        index = self._point_at(pos)

        if event.button() == Qt.MouseButton.RightButton:
            if index is not None and self._curve.remove_point(index):
                self._selected = None
                self.update()
                self.curveChanged.emit()
                self.pointCountChanged.emit(len(self._curve.points))
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # A plain click never creates a point - that would make the plot
        # hostile to click through. Adding is bound to the double click, which
        # is what the hint under the heading promises.
        self._selected = index
        self._drag_index = index
        if index is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self.interactive or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if self._point_at(pos) is not None:
            return
        temp, duty = self._to_value(pos)
        point = self._curve.add_point(temp, duty)
        self._selected = self._curve.points.index(point)
        self._drag_index = None
        self.update()
        self.curveChanged.emit()
        self.pointCountChanged.emit(len(self._curve.points))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.interactive:
            return
        pos = event.position()
        if self._drag_index is not None:
            temp, duty = self._to_value(pos)
            self._curve.move_point(self._drag_index, temp, duty)
            self.update()
            self.curveChanged.emit()
            return

        hover = self._point_at(pos)
        if hover != self._hover_index:
            self._hover_index = hover
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if hover is not None else Qt.CursorShape.CrossCursor
            )
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_index is not None:
            self._drag_index = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.curveChanged.emit()
            self.update()

    def keyPressEvent(self, event) -> None:
        if not self.interactive or self._selected is None:
            super().keyPressEvent(event)
            return
        key = event.key()
        point = self._curve.points[self._selected]
        step = 5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._curve.remove_point(self._selected):
                self._selected = None
                self.curveChanged.emit()
                self.pointCountChanged.emit(len(self._curve.points))
                self.update()
            return
        delta = {
            Qt.Key.Key_Left: (-step, 0),
            Qt.Key.Key_Right: (step, 0),
            Qt.Key.Key_Up: (0, step),
            Qt.Key.Key_Down: (0, -step),
        }.get(key)
        if delta is None:
            super().keyPressEvent(event)
            return
        self._curve.move_point(self._selected, point.temp + delta[0], point.duty + delta[1])
        self.curveChanged.emit()
        self.update()

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()

        self._paint_background(painter)
        if not self.compact:
            self._paint_grid(painter, rect)

        self._paint_curve(painter, rect)

        if not self.compact:
            self._paint_live(painter, rect)
            self._paint_points(painter)
        painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        radius = 6 if self.compact else 10
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(PALETTE.surface_alt)))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius
        )

    def _paint_grid(self, painter: QPainter, rect: QRectF) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        painter.setFont(font)
        lo, hi = self._bounds()

        pen = QPen(QColor(PALETTE.border))
        pen.setWidthF(1.0)
        for duty in range(0, 101, 20):
            y = rect.bottom() - duty / 100.0 * rect.height()
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QPen(QColor(PALETTE.text_faint)))
            painter.drawText(
                QRectF(0, y - 9, rect.left() - 8, 18),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{duty}%",
            )

        step = 10
        temp = int(lo - lo % step)
        while temp <= hi:
            if temp >= lo:
                x = self._to_pixel(temp, 0).x()
                painter.setPen(pen)
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                painter.setPen(QPen(QColor(PALETTE.text_faint)))
                painter.drawText(
                    QRectF(x - 22, rect.bottom() + 4, 44, 16),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                    f"{temp}°",
                )
            temp += step

    def _curve_path(self, rect: QRectF) -> QPainterPath:
        lo, hi = self._bounds()
        points = self._curve.points
        path = QPainterPath()
        start = self._to_pixel(lo, points[0].duty)
        path.moveTo(start)
        for point in points:
            if point.temp <= lo:
                continue
            path.lineTo(self._to_pixel(point.temp, point.duty))
        path.lineTo(self._to_pixel(hi, points[-1].duty))
        return path

    def _paint_curve(self, painter: QPainter, rect: QRectF) -> None:
        path = self._curve_path(rect)

        fill = QPainterPath(path)
        fill.lineTo(QPointF(rect.right(), rect.bottom()))
        fill.lineTo(QPointF(rect.left(), rect.bottom()))
        fill.closeSubpath()

        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        top = QColor(self._accent)
        top.setAlpha(90 if not self.compact else 70)
        bottom = QColor(self._accent)
        bottom.setAlpha(0)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(fill)

        pen = QPen(self._accent)
        pen.setWidthF(2.0 if self.compact else 2.4)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _paint_points(self, painter: QPainter) -> None:
        for index, point in enumerate(self._curve.points):
            centre = self._to_pixel(point.temp, point.duty)
            active = index in (self._hover_index, self._drag_index, self._selected)
            radius = 6.5 if active else 4.8

            painter.setPen(QPen(QColor(PALETTE.bg), 2))
            painter.setBrush(QBrush(self._accent if active else QColor(PALETTE.text)))
            painter.drawEllipse(centre, radius, radius)

            if active:
                self._paint_chip(
                    painter,
                    centre + QPointF(0, -22),
                    f"{point.temp:.0f} °C · {point.duty:.0f} %",
                )

    def _paint_live(self, painter: QPainter, rect: QRectF) -> None:
        if self._live_temp is None:
            return
        lo, hi = self._bounds()
        temp = max(lo, min(hi, self._live_temp))
        x = self._to_pixel(temp, 0).x()
        colour = temperature_colour(self._live_temp)

        pen = QPen(colour)
        pen.setWidthF(1.4)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        duty = self._live_duty
        if duty is None:
            duty = self._curve.duty_at(self._live_temp)
        centre = self._to_pixel(temp, duty)
        painter.setPen(QPen(QColor(PALETTE.bg), 2))
        painter.setBrush(QBrush(colour))
        painter.drawEllipse(centre, 6.0, 6.0)

        self._paint_chip(
            painter,
            QPointF(x, rect.top() + 12),
            f"{self._live_temp:.1f} °C → {duty:.0f} %",
            background=colour,
        )

    def _paint_chip(
        self,
        painter: QPainter,
        centre: QPointF,
        text: str,
        *,
        background: QColor | None = None,
    ) -> None:
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 14
        height = metrics.height() + 6
        rect = QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height)

        plot = self._plot_rect()
        if rect.left() < plot.left():
            rect.moveLeft(plot.left())
        if rect.right() > plot.right():
            rect.moveRight(plot.right())

        chip = QColor(background or PALETTE.surface_hover)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(chip))
        painter.drawRoundedRect(rect, height / 2, height / 2)

        luminance = 0.299 * chip.red() + 0.587 * chip.green() + 0.114 * chip.blue()
        painter.setPen(QPen(QColor("#10121a") if luminance > 150 else QColor(PALETTE.text)))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
