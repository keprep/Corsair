"""Programmatically drawn icons - the app ships without binary assets.

Everything is painted on a 24x24 grid and scaled, so the same glyph works as a
16 px list icon and as a 256 px application icon without going blurry.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from corsair_control.ui.theme import PALETTE

GRID = 24.0


def _stroke(painter: QPainter, colour: QColor, width: float = 1.9) -> None:
    pen = QPen(colour)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _fill(painter: QPainter, colour: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(colour))


# ----------------------------------------------------------------------
# glyphs, each drawn into a 24x24 box
# ----------------------------------------------------------------------
def _glyph_fan(painter: QPainter, colour: QColor) -> None:
    _fill(painter, colour)
    painter.save()
    painter.translate(12, 12)
    for _ in range(3):
        blade = QPainterPath()
        blade.moveTo(0, 0)
        blade.cubicTo(1.7, -4.6, 8.0, -5.4, 8.4, -0.7)
        blade.cubicTo(5.9, 1.9, 2.1, 1.7, 0, 0)
        painter.drawPath(blade)
        painter.rotate(120)
    painter.restore()


def _glyph_pump(painter: QPainter, colour: QColor) -> None:
    """A droplet - the loop, not the impeller."""
    _fill(painter, colour)
    drop = QPainterPath()
    drop.moveTo(12, 3.5)
    drop.cubicTo(17.5, 10.0, 19.5, 12.6, 19.5, 15.2)
    drop.arcTo(QRectF(4.5, 10.6, 15.0, 10.0), 0, -180)
    drop.cubicTo(4.5, 12.6, 6.5, 10.0, 12, 3.5)
    painter.drawPath(drop)


def _glyph_dashboard(painter: QPainter, colour: QColor) -> None:
    _stroke(painter, colour)
    painter.drawRoundedRect(QRectF(3.5, 3.5, 7.0, 7.0), 1.8, 1.8)
    painter.drawRoundedRect(QRectF(13.5, 3.5, 7.0, 7.0), 1.8, 1.8)
    painter.drawRoundedRect(QRectF(3.5, 13.5, 7.0, 7.0), 1.8, 1.8)
    painter.drawRoundedRect(QRectF(13.5, 13.5, 7.0, 7.0), 1.8, 1.8)


def _glyph_device(painter: QPainter, colour: QColor) -> None:
    """A radiator: an outline with fins."""
    _stroke(painter, colour)
    painter.drawRoundedRect(QRectF(3.0, 5.0, 18.0, 14.0), 2.4, 2.4)
    for x in (8.0, 12.0, 16.0):
        painter.drawLine(QPointF(x, 7.6), QPointF(x, 16.4))


def _glyph_lighting(painter: QPainter, colour: QColor) -> None:
    _stroke(painter, colour)
    painter.drawEllipse(QPointF(12, 12), 4.0, 4.0)
    for index in range(8):
        painter.save()
        painter.translate(12, 12)
        painter.rotate(index * 45)
        painter.drawLine(QPointF(0, -7.0), QPointF(0, -9.2))
        painter.restore()


def _glyph_settings(painter: QPainter, colour: QColor) -> None:
    _fill(painter, colour)
    painter.save()
    painter.translate(12, 12)
    for _ in range(8):
        painter.drawRoundedRect(QRectF(-1.5, -9.6, 3.0, 4.6), 1.1, 1.1)
        painter.rotate(45)
    painter.restore()
    _stroke(painter, colour, 2.2)
    painter.drawEllipse(QPointF(12, 12), 5.4, 5.4)
    painter.drawEllipse(QPointF(12, 12), 2.2, 2.2)


def _glyph_cpu(painter: QPainter, colour: QColor) -> None:
    _stroke(painter, colour)
    painter.drawRoundedRect(QRectF(6.0, 6.0, 12.0, 12.0), 2.0, 2.0)
    painter.drawRect(QRectF(9.6, 9.6, 4.8, 4.8))
    for offset in (8.8, 12.0, 15.2):
        painter.drawLine(QPointF(offset, 3.2), QPointF(offset, 6.0))
        painter.drawLine(QPointF(offset, 18.0), QPointF(offset, 20.8))
        painter.drawLine(QPointF(3.2, offset), QPointF(6.0, offset))
        painter.drawLine(QPointF(18.0, offset), QPointF(20.8, offset))


def _glyph_gpu(painter: QPainter, colour: QColor) -> None:
    _stroke(painter, colour)
    painter.drawRoundedRect(QRectF(2.8, 6.5, 18.4, 11.0), 2.0, 2.0)
    painter.drawEllipse(QPointF(8.6, 12.0), 3.0, 3.0)
    painter.drawEllipse(QPointF(16.2, 12.0), 3.0, 3.0)


def _glyph_chart(painter: QPainter, colour: QColor) -> None:
    _stroke(painter, colour)
    path = QPainterPath()
    path.moveTo(3.5, 16.5)
    path.lineTo(9.0, 11.0)
    path.lineTo(13.0, 14.5)
    path.lineTo(20.5, 6.5)
    painter.drawPath(path)


def _glyph_clock(painter: QPainter, colour: QColor) -> None:
    _stroke(painter, colour)
    painter.drawEllipse(QPointF(12, 12), 8.4, 8.4)
    painter.drawLine(QPointF(12, 6.8), QPointF(12, 12))
    painter.drawLine(QPointF(12, 12), QPointF(15.8, 14.2))


def _glyph_bell(painter: QPainter, colour: QColor) -> None:
    _stroke(painter, colour)
    path = QPainterPath()
    path.moveTo(5.6, 16.4)
    path.lineTo(18.4, 16.4)
    path.lineTo(16.6, 13.8)
    path.lineTo(16.6, 10.4)
    path.cubicTo(16.6, 7.0, 14.4, 5.2, 12.0, 5.2)
    path.cubicTo(9.6, 5.2, 7.4, 7.0, 7.4, 10.4)
    path.lineTo(7.4, 13.8)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawLine(QPointF(10.4, 18.6), QPointF(13.6, 18.6))


def _glyph_pause(painter: QPainter, colour: QColor) -> None:
    _fill(painter, colour)
    painter.drawRoundedRect(QRectF(7.0, 5.5, 3.6, 13.0), 1.6, 1.6)
    painter.drawRoundedRect(QRectF(13.4, 5.5, 3.6, 13.0), 1.6, 1.6)


def _glyph_play(painter: QPainter, colour: QColor) -> None:
    _fill(painter, colour)
    path = QPainterPath()
    path.moveTo(7.5, 5.0)
    path.lineTo(19.0, 12.0)
    path.lineTo(7.5, 19.0)
    path.closeSubpath()
    painter.drawPath(path)


GLYPHS = {
    "fan": _glyph_fan,
    "pump": _glyph_pump,
    "dashboard": _glyph_dashboard,
    "device": _glyph_device,
    "lighting": _glyph_lighting,
    "settings": _glyph_settings,
    "cpu": _glyph_cpu,
    "gpu": _glyph_gpu,
    "chart": _glyph_chart,
    "clock": _glyph_clock,
    "bell": _glyph_bell,
    "pause": _glyph_pause,
    "play": _glyph_play,
}

#: Sensor categories map onto the glyphs above.
CATEGORY_GLYPHS = {
    "cpu": "cpu",
    "gpu": "gpu",
    "liquid": "pump",
    "virtual": "chart",
    "storage": "device",
}


def glyph_pixmap(name: str, colour: str | QColor | None = None, size: int = 24) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / GRID, size / GRID)
    draw = GLYPHS.get(name, _glyph_fan)
    draw(painter, QColor(colour or PALETTE.text_dim))
    painter.end()
    return pixmap


def glyph_icon(name: str, colour: str | QColor | None = None, size: int = 40) -> QIcon:
    icon = QIcon()
    for scale in (1.0, 1.5, 2.0):
        icon.addPixmap(glyph_pixmap(name, colour, int(size * scale)))
    return icon


# ----------------------------------------------------------------------
# application icon
# ----------------------------------------------------------------------
def fan_pixmap(size: int = 64, colour: str | None = None, background: bool = True) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    accent = QColor(colour or PALETTE.accent)

    if background:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(PALETTE.surface_alt)))
        painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)

    painter.scale(size / GRID, size / GRID)
    _glyph_fan(painter, accent)

    painter.setBrush(QBrush(QColor(PALETTE.bg if background else "#12141a")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(12, 12), 2.0, 2.0)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(fan_pixmap(size))
    return icon


def tray_icon(colour: str | None = None) -> QIcon:
    icon = QIcon()
    for size in (22, 32, 48):
        icon.addPixmap(fan_pixmap(size, colour=colour, background=False))
    return icon
