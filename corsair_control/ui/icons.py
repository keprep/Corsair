"""Programmatically drawn icons - the app ships without binary assets."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPixmap

from corsair_control.ui.theme import PALETTE


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

    centre = QPointF(size / 2, size / 2)
    radius = size * 0.34
    painter.translate(centre)
    painter.setBrush(QBrush(accent))

    for _ in range(3):
        blade = QPainterPath()
        blade.moveTo(0, 0)
        blade.cubicTo(
            radius * 0.2,
            -radius * 0.55,
            radius * 0.95,
            -radius * 0.65,
            radius,
            -radius * 0.08,
        )
        blade.cubicTo(radius * 0.7, radius * 0.22, radius * 0.25, radius * 0.2, 0, 0)
        painter.drawPath(blade)
        painter.rotate(120)

    painter.setBrush(QBrush(QColor(PALETTE.bg)))
    painter.drawEllipse(QPointF(0, 0), size * 0.09, size * 0.09)
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
