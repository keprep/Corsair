"""Headless smoke tests for the Qt layer (QT_QPA_PLATFORM=offscreen)."""

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from corsair_control.core.config import Settings  # noqa: E402
from corsair_control.core.curve import CurvePoint, FanCurve  # noqa: E402
from corsair_control.core.engine import ControlEngine  # noqa: E402
from corsair_control.core.profile import ProfileStore  # noqa: E402
from corsair_control.ui.main_window import MainWindow  # noqa: E402
from corsair_control.ui.widgets.curve_editor import CurveWidget  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path):
    settings = Settings(poll_interval=0.5, close_to_tray=False)
    store = ProfileStore(tmp_path / "profiles.json")
    store.load()
    engine = ControlEngine(settings, store, demo=True)
    engine.discover()
    window = MainWindow(engine, settings, store, demo=True)
    yield window
    window.close()
    engine.stop()


def _click(widget, x, y, button=Qt.MouseButton.LeftButton):
    for event_type in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
        event = QMouseEvent(
            event_type,
            QPointF(x, y),
            QPointF(x, y),
            button,
            button,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(widget, event)


def _double_click(widget, x, y):
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def test_curve_widget_adds_a_point_on_double_click(qapp):
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 100)])
    widget = CurveWidget(curve)
    widget.resize(400, 300)

    before = len(curve.points)
    _double_click(widget, 200, 150)
    assert len(curve.points) == before + 1


def test_curve_widget_ignores_a_single_click_on_empty_space(qapp):
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 100)])
    widget = CurveWidget(curve)
    widget.resize(400, 300)

    _click(widget, 200, 150)
    assert len(curve.points) == 2


def test_curve_widget_removes_a_point_on_right_click(qapp):
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(50, 60), CurvePoint(70, 100)])
    widget = CurveWidget(curve)
    widget.resize(400, 300)

    pixel = widget._to_pixel(50, 60)
    _click(widget, pixel.x(), pixel.y(), Qt.MouseButton.RightButton)
    assert [p.temp for p in curve.points] == [30, 70]


def test_curve_widget_keeps_two_points_minimum(qapp):
    curve = FanCurve([CurvePoint(30, 20), CurvePoint(70, 100)])
    widget = CurveWidget(curve)
    widget.resize(400, 300)

    pixel = widget._to_pixel(30, 20)
    _click(widget, pixel.x(), pixel.y(), Qt.MouseButton.RightButton)
    assert len(curve.points) == 2


def test_curve_widget_renders(qapp):
    widget = CurveWidget(FanCurve())
    widget.resize(400, 300)
    widget.set_live(55.0, 60.0)
    widget.grab()  # forces a full paint pass


def test_window_builds_a_page_per_device(window):
    # dashboard + two demo devices + lighting + automation + settings
    assert window.stack.count() == 6
    assert len(window.device_pages) == 2
    # The navigation additionally carries two non-selectable section headers.
    selectable = [page for page in window._nav_pages if page >= 0]
    assert selectable == [0, 1, 2, 3, 4, 5]
    assert window.nav.count() == len(window._nav_pages) == 8


def test_section_headers_are_not_selectable(window):
    headers = [row for row, page in enumerate(window._nav_pages) if page < 0]
    assert headers
    for row in headers:
        assert window.nav.item(row).flags() == Qt.ItemFlag.NoItemFlags


def test_navigation_switches_pages(window):
    for row, page in enumerate(window._nav_pages):
        if page < 0:
            continue
        window.nav.setCurrentRow(row)
        assert window.stack.currentIndex() == page


def test_accent_change_rebuilds_without_losing_devices(window):
    window._on_accent_changed("#22c4d6")
    assert window.settings.accent == "#22c4d6"
    assert len(window.device_pages) == 2
    assert window.stack.count() == 6


def test_snapshot_updates_reach_the_widgets(window):
    snapshot = window.engine.tick()
    window._on_snapshot(snapshot)

    page = window.device_pages[snapshot.devices[0].key]
    card = page.cards["fan1"]
    assert "rpm" in card.rpm_label.text()
    assert window.status_label.text()


def test_profile_switch_rebinds_the_cards(window):
    window._activate_profile("Silent")
    page = next(iter(window.device_pages.values()))
    card = next(iter(page.cards.values()))
    assert card.config is window.store.active.device(page.device.key).channels[card.channel_id]


def test_editing_a_curve_marks_the_store_dirty(window, tmp_path):
    page = next(iter(window.device_pages.values()))
    page.panel.editor.curve.add_point(55, 66)
    page.panel._on_curve_changed()
    window._save_now()
    assert (tmp_path / "profiles.json").exists()


def test_dashboard_renders_rows(window):
    snapshot = window.engine.tick()
    window.dashboard.update_from(snapshot)
    assert window.dashboard._rows
    window.dashboard.grab()
