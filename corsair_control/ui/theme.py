"""Colour palette and stylesheet.

Everything visual is derived from a handful of tokens so that the accent
colour can be changed in one place and the custom-painted widgets stay in
sync with the stylesheet-driven ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QPalette


@dataclass
class Palette:
    bg: str = "#12141a"
    surface: str = "#191c24"
    surface_alt: str = "#1f232d"
    surface_hover: str = "#262b37"
    border: str = "#2c313d"
    text: str = "#e6e9f0"
    text_dim: str = "#9aa2b4"
    text_faint: str = "#6b7386"
    accent: str = "#f0a500"
    accent_dim: str = "#a87400"
    good: str = "#3ecf8e"
    warn: str = "#f4a13c"
    bad: str = "#ef4d5a"
    cool: str = "#4aa8ff"
    pump: str = "#8b7cf6"

    def q(self, name: str, alpha: int = 255) -> QColor:
        colour = QColor(getattr(self, name))
        colour.setAlpha(alpha)
        return colour


PALETTE = Palette()

#: Accent colours offered in the settings. Corsair yellow is the default
#: because it is what the hardware looks like out of the box.
ACCENTS: dict[str, str] = {
    "Amber": "#f0a500",
    "Cyan": "#22c4d6",
    "Violet": "#8b7cf6",
    "Green": "#3ecf8e",
    "Red": "#ef4d5a",
    "Blue": "#4aa8ff",
}


def mix(first: str | QColor, second: str | QColor, ratio: float) -> QColor:
    """Blend two colours; ``ratio`` 0 returns *first*, 1 returns *second*."""
    a, b = QColor(first), QColor(second)
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(a.red() + (b.red() - a.red()) * ratio),
        int(a.green() + (b.green() - a.green()) * ratio),
        int(a.blue() + (b.blue() - a.blue()) * ratio),
    )


def set_accent(colour: str, palette: Palette = PALETTE) -> None:
    """Switch the accent at runtime.

    The custom-painted widgets read ``PALETTE.accent`` while painting, so they
    pick the new colour up on the next repaint; the stylesheet has to be
    rebuilt and reapplied by the caller.
    """
    palette.accent = colour
    palette.accent_dim = mix(colour, palette.bg, 0.45).name()


def temperature_colour(temp: float | None, palette: Palette = PALETTE) -> QColor:
    """Blue below 45 °C, amber around 70 °C, red past 85 °C."""
    if temp is None:
        return palette.q("text_faint")
    stops = [
        (30.0, QColor(palette.cool)),
        (50.0, QColor(palette.good)),
        (70.0, QColor(palette.warn)),
        (85.0, QColor(palette.bad)),
    ]
    if temp <= stops[0][0]:
        return stops[0][1]
    if temp >= stops[-1][0]:
        return stops[-1][1]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= temp <= t1:
            ratio = (temp - t0) / (t1 - t0)
            return QColor(
                int(c0.red() + (c1.red() - c0.red()) * ratio),
                int(c0.green() + (c1.green() - c0.green()) * ratio),
                int(c0.blue() + (c1.blue() - c0.blue()) * ratio),
            )
    return stops[-1][1]


def dark_palette(palette: Palette = PALETTE) -> QPalette:
    """A dark QPalette to go with the stylesheet.

    Widgets that paint themselves from the palette rather than from the
    stylesheet - scroll-area viewports above all - would otherwise show up as
    bright rectangles on an otherwise dark window.
    """
    p = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    p.setColor(role.Window, QColor(palette.bg))
    p.setColor(role.WindowText, QColor(palette.text))
    p.setColor(role.Base, QColor(palette.surface))
    p.setColor(role.AlternateBase, QColor(palette.surface_alt))
    p.setColor(role.Text, QColor(palette.text))
    p.setColor(role.PlaceholderText, QColor(palette.text_faint))
    p.setColor(role.Button, QColor(palette.surface_alt))
    p.setColor(role.ButtonText, QColor(palette.text))
    p.setColor(role.BrightText, QColor("#ffffff"))
    p.setColor(role.Highlight, QColor(palette.accent))
    p.setColor(role.HighlightedText, QColor("#1a1400"))
    p.setColor(role.ToolTipBase, QColor(palette.surface_alt))
    p.setColor(role.ToolTipText, QColor(palette.text))
    p.setColor(role.Link, QColor(palette.accent))

    for disabled in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(group.Disabled, disabled, QColor(palette.text_faint))
    return p


def stylesheet(palette: Palette = PALETTE) -> str:
    p = palette
    return f"""
    /* Only containers get a background. A blanket "QWidget {{ background }}"
       would make every QLabel paint an opaque rectangle over the card it sits
       on. */
    QWidget {{
        color: {p.text};
        font-size: 13px;
    }}
    QMainWindow, QDialog, QMessageBox, QInputDialog {{
        background: {p.bg};
    }}
    QLabel, QCheckBox, QRadioButton, QSplitter, QSplitter::handle {{
        background: transparent;
    }}
    QMenu {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 22px 6px 12px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background: {p.surface_hover};
    }}
    QMenu::separator {{
        height: 1px;
        background: {p.border};
        margin: 4px 6px;
    }}
    /* Deliberately no QToolTip rule here: Qt implements tooltips as QLabel,
       and a QToolTip selector in a global stylesheet leaks onto every plain
       label in the window. */

    #Sidebar {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {mix(p.surface, p.accent, 0.05).name()}, stop:0.35 {p.surface});
        border-right: 1px solid {p.border};
    }}
    #SidebarTitle {{
        font-size: 16px;
        font-weight: 700;
        letter-spacing: 0.3px;
        color: {p.text};
    }}
    #SidebarSubtitle {{
        color: {p.text_faint};
        font-size: 11px;
    }}
    #NavSection {{
        color: {p.text_faint};
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 1.2px;
        padding: 10px 18px 2px 18px;
    }}
    #NavList {{
        background: transparent;
        border: none;
        outline: none;
        padding: 4px 8px;
    }}
    #NavList::item {{
        padding: 9px 10px;
        margin: 2px 4px;
        border-radius: 9px;
        border-left: 3px solid transparent;
        color: {p.text_dim};
    }}
    #NavList::item:selected {{
        background: {mix(p.surface_hover, p.accent, 0.14).name()};
        border-left: 3px solid {p.accent};
        color: {p.text};
    }}
    #NavList::item:hover {{
        background: {p.surface_alt};
    }}

    QLabel#SectionTitle {{
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 0.2px;
        color: {p.text};
    }}
    QLabel#Hint, QLabel#Dim {{
        color: {p.text_dim};
    }}
    QLabel#Faint {{
        color: {p.text_faint};
        font-size: 11px;
    }}
    QLabel#PageTitle {{
        font-size: 20px;
        font-weight: 700;
        letter-spacing: 0.2px;
    }}
    QLabel#Metric {{
        font-size: 16px;
        font-weight: 700;
    }}

    #Card {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {mix(p.surface, "#ffffff", 0.03).name()}, stop:1 {p.surface});
        border: 1px solid {p.border};
        border-radius: 14px;
    }}
    #Card:hover {{
        border: 1px solid {mix(p.border, p.accent, 0.35).name()};
    }}
    #CardTitle {{
        font-weight: 600;
        font-size: 13px;
    }}
    #Toast {{
        background: {mix(p.surface_alt, p.good, 0.25).name()};
        border: 1px solid {p.good};
        border-radius: 9px;
        padding: 4px 10px;
        color: {p.text};
        font-size: 11px;
    }}

    QPushButton {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {mix(p.surface_alt, "#ffffff", 0.05).name()}, stop:1 {p.surface_alt});
        border: 1px solid {p.border};
        border-radius: 9px;
        padding: 7px 14px;
        color: {p.text};
    }}
    QPushButton:hover {{
        background: {p.surface_hover};
        border: 1px solid {mix(p.border, p.accent, 0.4).name()};
    }}
    QPushButton:pressed {{ background: {p.border}; }}
    QPushButton:disabled {{ color: {p.text_faint}; background: {p.surface}; }}
    QPushButton#Accent {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {mix(p.accent, "#ffffff", 0.18).name()}, stop:1 {p.accent});
        border: 1px solid {p.accent};
        color: #1a1400;
        font-weight: 600;
    }}
    QPushButton#Accent:hover {{ background: {mix(p.accent, "#ffffff", 0.3).name()}; }}
    QPushButton#Danger:hover {{ background: {p.bad}; color: #14060a; }}
    QPushButton#Ghost {{
        background: transparent;
        border: 1px solid {p.border};
    }}

    QComboBox {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px 10px;
        min-height: 18px;
    }}
    QComboBox:hover {{ border: 1px solid {p.surface_hover}; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        selection-background-color: {p.surface_hover};
        outline: none;
        padding: 4px;
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px 8px;
        selection-background-color: {p.accent_dim};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {p.accent};
    }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border-radius: 4px;
        border: 1px solid {p.border};
        background: {p.surface_alt};
    }}
    QCheckBox::indicator:checked {{
        background: {p.accent};
        border: 1px solid {p.accent};
    }}

    QSlider::groove:horizontal {{
        height: 6px;
        background: {p.surface_alt};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {p.accent};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: {p.text};
        width: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{ background: #ffffff; }}
    QSlider:disabled::sub-page:horizontal {{ background: {p.border}; }}

    /* The viewport of a scroll area paints itself from the style, not from
       the rule above, so it has to be addressed explicitly - otherwise it
       stays light grey on a dark window. */
    QAbstractScrollArea {{ border: none; background: transparent; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget {{ background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p.surface_hover}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {p.border}; border-radius: 5px; min-width: 30px;
    }}

    #StatusBar {{
        background: {p.surface};
        border-top: 1px solid {p.border};
        color: {p.text_dim};
    }}
    #Banner {{
        background: rgba(239, 77, 90, 0.14);
        border: 1px solid {p.bad};
        border-radius: 10px;
        padding: 10px 12px;
    }}
    #BannerInfo {{
        background: rgba(240, 165, 0, 0.12);
        border: 1px solid {p.accent_dim};
        border-radius: 10px;
        padding: 10px 12px;
    }}

    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{
        background: transparent;
        color: {p.text_dim};
        padding: 8px 14px;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{
        color: {p.text};
        border-bottom: 2px solid {p.accent};
    }}
    """
