"""Main window: navigation, profiles, and the bridge to the control engine."""

from __future__ import annotations

import logging

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QCloseEvent, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from corsair_control.core.config import Settings
from corsair_control.core.engine import ControlEngine, Snapshot
from corsair_control.core.profile import ProfileStore
from corsair_control.ui.i18n import tr
from corsair_control.ui.icons import app_icon, fan_pixmap, glyph_icon, tray_icon
from corsair_control.ui.pages.dashboard import DashboardPage
from corsair_control.ui.pages.device_page import DevicePage
from corsair_control.ui.pages.lighting import LightingPage
from corsair_control.ui.pages.settings_page import SettingsPage
from corsair_control.ui.theme import PALETTE, dark_palette, set_accent, stylesheet
from corsair_control.version import APP_NAME, __version__

log = logging.getLogger(__name__)

SAVE_DEBOUNCE_MS = 900


class EngineBridge(QObject):
    """Moves snapshots from the engine thread into the Qt event loop."""

    snapshotReady = pyqtSignal(object)


class MainWindow(QMainWindow):
    def __init__(
        self,
        engine: ControlEngine,
        settings: Settings,
        store: ProfileStore,
        *,
        demo: bool = False,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.settings = settings
        self.store = store
        self.demo = demo
        self.device_pages: dict[str, DevicePage] = {}
        #: nav row -> stack index; -1 marks a section header
        self._nav_pages: list[int] = []
        self._nav_items: dict[str, QListWidgetItem] = {}
        self._current_nav_row = 0
        self._fade: QPropertyAnimation | None = None

        self.setWindowTitle(f"{APP_NAME} {__version__}" + (" — Demo" if demo else ""))
        self.setWindowIcon(app_icon())
        self.resize(1220, 780)
        self.setMinimumSize(940, 620)
        self.setPalette(dark_palette())
        self.setStyleSheet(stylesheet())

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_now)

        self._build_ui()
        self._build_tray()

        self.bridge = EngineBridge()
        self.bridge.snapshotReady.connect(self._on_snapshot)
        self.engine.subscribe(self.bridge.snapshotReady.emit)

        self.rebuild_devices()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.banner = QLabel("")
        self.banner.setObjectName("Banner")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        banner_wrap = QWidget()
        banner_layout = QVBoxLayout(banner_wrap)
        banner_layout.setContentsMargins(20, 12, 20, 0)
        banner_layout.addWidget(self.banner)
        self.banner_wrap = banner_wrap
        banner_wrap.setVisible(False)
        right_layout.addWidget(banner_wrap)

        self.stack = QStackedWidget()
        right_layout.addWidget(self.stack, 1)
        right_layout.addWidget(self._build_status_bar())

        root.addWidget(right, 1)
        self.setCentralWidget(central)

        self.dashboard = DashboardPage(
            lambda: self.engine.sensors.sensors, self.settings.history_seconds
        )
        self.lighting_page = LightingPage()
        self.lighting_page.applyRequested.connect(self._apply_lighting)
        self.settings_page = SettingsPage(self.settings)
        self.settings_page.settingsChanged.connect(self._on_settings_changed)
        self.settings_page.accentChanged.connect(self._on_accent_changed)
        self.settings_page.rescanRequested.connect(self.rescan)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(258)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(16, 16, 16, 10)
        brand_layout.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(fan_pixmap(30))
        brand_layout.addWidget(logo)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        title = QLabel(APP_NAME)
        title.setObjectName("SidebarTitle")
        brand_text.addWidget(title)
        subtitle = QLabel(
            tr("Demo mode - no hardware is being controlled") if self.demo else f"v{__version__}"
        )
        subtitle.setObjectName("SidebarSubtitle")
        subtitle.setWordWrap(True)
        brand_text.addWidget(subtitle)
        brand_layout.addLayout(brand_text, 1)
        layout.addWidget(brand)

        profile_wrap = QWidget()
        profile_layout = QVBoxLayout(profile_wrap)
        profile_layout.setContentsMargins(14, 6, 14, 10)
        profile_layout.setSpacing(6)
        profile_layout.addWidget(QLabel(tr("Profile")))

        row = QHBoxLayout()
        row.setSpacing(6)
        self.profile_box = QComboBox()
        self.profile_box.currentIndexChanged.connect(self._on_profile_selected)
        row.addWidget(self.profile_box, 1)

        menu_button = QPushButton("⋯")
        menu_button.setFixedWidth(34)
        menu_button.setObjectName("Ghost")
        menu = QMenu(self)
        for label, slot in (
            ("New profile", self._new_profile),
            ("Duplicate profile", self._duplicate_profile),
            ("Rename profile", self._rename_profile),
            ("Delete profile", self._delete_profile),
        ):
            action = QAction(tr(label), self)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu_button.setMenu(menu)
        row.addWidget(menu_button)
        profile_layout.addLayout(row)
        layout.addWidget(profile_wrap)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setUniformItemSizes(False)
        self.nav.currentRowChanged.connect(self._on_nav)
        layout.addWidget(self.nav, 1)

        self.pause_button = QPushButton(
            tr("Resume control") if self.engine.paused else tr("Pause control")
        )
        self.pause_button.setIcon(
            glyph_icon("play" if self.engine.paused else "pause", PALETTE.text_dim, 18)
        )
        self.pause_button.clicked.connect(self._toggle_pause)
        wrap = QWidget()
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(14, 0, 14, 0)
        wrap_layout.addWidget(self.pause_button)
        layout.addWidget(wrap)

        return sidebar

    def _nav_header(self, text: str) -> None:
        """Add a non-selectable section label to the navigation list."""
        item = QListWidgetItem(text.upper())
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, self.font().pointSizeF() - 2.0))
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(PALETTE.text_faint))
        self.nav.addItem(item)
        self._nav_pages.append(-1)

    def _nav_entry(self, text: str, glyph: str, page: int, key: str | None = None) -> None:
        item = QListWidgetItem(glyph_icon(glyph, PALETTE.text_dim, 18), text)
        item.setData(Qt.ItemDataRole.UserRole, glyph)
        self.nav.addItem(item)
        self._nav_pages.append(page)
        if key is not None:
            self._nav_items[key] = item

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(32)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(14)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label, 1)

        self.saved_label = QLabel("")
        self.saved_label.setObjectName("Toast")
        self.saved_label.setVisible(False)
        layout.addWidget(self.saved_label)

        self.profile_label = QLabel("")
        self.profile_label.setObjectName("Faint")
        layout.addWidget(self.profile_label)
        return bar

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()

        show = QAction(tr("Show window"), self)
        show.triggered.connect(self._restore_window)
        menu.addAction(show)

        self.tray_profiles = QMenu(tr("Profile"), self)
        menu.addMenu(self.tray_profiles)

        menu.addSeparator()
        quit_action = QAction(tr("Quit"), self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore_window()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()

    # ------------------------------------------------------------------
    # device pages
    # ------------------------------------------------------------------
    def rebuild_devices(self) -> None:
        self.nav.blockSignals(True)
        self.nav.clear()
        self._nav_pages.clear()
        self._nav_items.clear()
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
        for page in self.device_pages.values():
            page.setParent(None)
            page.deleteLater()
        self.device_pages.clear()

        self.stack.addWidget(self.dashboard)
        self._nav_entry(tr("Dashboard"), "dashboard", 0)

        profile = self.store.active
        if self.engine.devices:
            self._nav_header(tr("Hardware"))
        for device in self.engine.devices:
            page = DevicePage(
                device, profile.device(device.key), lambda: self.engine.sensors.sensors
            )
            page.configChanged.connect(self._on_config_changed)
            page.overrideChanged.connect(self._on_override)
            page.pumpModeChanged.connect(self._on_pump_mode)
            self.device_pages[device.key] = page
            index = self.stack.count()
            self.stack.addWidget(page)
            glyph = "pump" if any(c.is_pump for c in device.channels) else "device"
            self._nav_entry(device.short_name, glyph, index, key=device.key)

        self._nav_header(tr("System"))
        self.stack.addWidget(self.lighting_page)
        self._nav_entry(tr("Lighting"), "lighting", self.stack.count() - 1)
        self.stack.addWidget(self.settings_page)
        self._nav_entry(tr("Settings"), "settings", self.stack.count() - 1)

        self.lighting_page.rebuild(
            self.engine.devices, {d.key: profile.device(d.key) for d in self.engine.devices}
        )
        self.dashboard.refresh_sensors()

        self.nav.blockSignals(False)
        first = next((row for row, page in enumerate(self._nav_pages) if page >= 0), 0)
        self.nav.setCurrentRow(first)

        self._refresh_profile_box()
        self._show_startup_problems()

    def _show_startup_problems(self) -> None:
        errors = list(self.engine.startup_errors)
        if self.engine.devices or not errors:
            self.banner_wrap.setVisible(bool(errors))
            self.banner.setVisible(bool(errors))
            self.banner.setText("\n".join(errors))
            return
        text = tr("No supported Corsair device was found.")
        detail = "\n".join(errors)
        hint = tr("Start the application with --demo to explore the interface.")
        self.banner.setText(f"{text}\n{detail}\n{hint}")
        self.banner.setVisible(True)
        self.banner_wrap.setVisible(True)

    def rescan(self) -> None:
        self.engine.discover()
        self.rebuild_devices()

    # ------------------------------------------------------------------
    # profiles
    # ------------------------------------------------------------------
    def _refresh_profile_box(self) -> None:
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for name in self.store.names():
            self.profile_box.addItem(name)
        index = self.profile_box.findText(self.store.active_name)
        if index >= 0:
            self.profile_box.setCurrentIndex(index)
        self.profile_box.blockSignals(False)
        self.profile_label.setText(f"{tr('Profile')}: {self.store.active_name}")
        self._refresh_tray_profiles()

    def _refresh_tray_profiles(self) -> None:
        if self.tray is None:
            return
        self.tray_profiles.clear()
        for name in self.store.names():
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(name == self.store.active_name)
            action.triggered.connect(lambda _checked, n=name: self._activate_profile(n))
            self.tray_profiles.addAction(action)

    def _on_profile_selected(self, index: int) -> None:
        name = self.profile_box.itemText(index)
        if name and name != self.store.active_name:
            self._activate_profile(name)

    def _activate_profile(self, name: str) -> None:
        self.engine.set_profile(name)
        profile = self.store.active
        for key, page in self.device_pages.items():
            page.set_config(profile.device(key))
        self.lighting_page.rebuild(
            self.engine.devices, {d.key: profile.device(d.key) for d in self.engine.devices}
        )
        self._refresh_profile_box()
        self._schedule_save()

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, tr("New profile"), tr("Profile name"))
        if ok and name.strip():
            self.store.add(name.strip())
            self._activate_profile(name.strip())

    def _duplicate_profile(self) -> None:
        base = self.store.active_name
        name, ok = QInputDialog.getText(
            self, tr("Duplicate profile"), tr("Profile name"), text=f"{base} 2"
        )
        if ok and name.strip():
            self.store.add(name.strip(), copy_from=base)
            self._activate_profile(name.strip())

    def _rename_profile(self) -> None:
        old = self.store.active_name
        name, ok = QInputDialog.getText(self, tr("Rename profile"), tr("Profile name"), text=old)
        if ok and self.store.rename(old, name.strip()):
            self._refresh_profile_box()
            self._schedule_save()

    def _delete_profile(self) -> None:
        name = self.store.active_name
        answer = QMessageBox.question(self, tr("Delete profile"), f"{tr('Delete profile')}: {name}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.store.remove(name):
            self._activate_profile(self.store.active_name)

    # ------------------------------------------------------------------
    # engine interaction
    # ------------------------------------------------------------------
    def _on_nav(self, row: int) -> None:
        if not 0 <= row < len(self._nav_pages):
            return
        page = self._nav_pages[row]
        if page < 0 or page >= self.stack.count():
            # Landing on a section header (only reachable programmatically)
            # would silently drop the highlight, so bounce back.
            QTimer.singleShot(0, lambda: self.nav.setCurrentRow(self._current_nav_row))
            return
        self._current_nav_row = row
        self.stack.setCurrentIndex(page)
        self._highlight_nav(row)
        self._fade_in(self.stack.currentWidget())

    def _highlight_nav(self, current: int) -> None:
        """Tint the selected entry's icon with the accent colour."""
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            glyph = item.data(Qt.ItemDataRole.UserRole)
            if not glyph:
                continue
            colour = PALETTE.accent if row == current else PALETTE.text_dim
            item.setIcon(glyph_icon(glyph, colour, 18))

    def _fade_in(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(160)
        animation.setStartValue(0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Dropping the effect afterwards keeps the widget on the plain paint
        # path; a lingering QGraphicsEffect costs a full repaint every frame.
        animation.finished.connect(lambda: widget.setGraphicsEffect(None))
        self._fade = animation
        animation.start()

    def _on_config_changed(self, device_key: str, channel_id: str) -> None:
        self.engine.config_changed(device_key, channel_id)
        self._schedule_save()

    def _on_override(self, device_key: str, channel_id: str, duty: object) -> None:
        self.engine.set_override(
            device_key, channel_id, None if duty is None else float(duty)  # type: ignore[arg-type]
        )

    def _on_pump_mode(self, device_key: str, mode: str) -> None:
        error = self.engine.apply_pump_mode(device_key, mode)
        if error:
            self.status_label.setText(error)
        self._schedule_save()

    def _apply_lighting(self, device_key: str, channel_id: str, mode: str, colours: list) -> None:
        error = self.engine.apply_lighting(device_key, channel_id, mode, colours)
        self.lighting_page.show_result(device_key, error)
        self._schedule_save()

    def _toggle_pause(self) -> None:
        self.engine.pause(not self.engine.paused)
        self.pause_button.setText(
            tr("Resume control") if self.engine.paused else tr("Pause control")
        )
        self.pause_button.setIcon(
            glyph_icon(
                "play" if self.engine.paused else "pause",
                PALETTE.warn if self.engine.paused else PALETTE.text_dim,
                18,
            )
        )

    def _on_accent_changed(self, colour: str) -> None:
        self.settings.accent = colour
        self.settings.save()
        set_accent(colour)
        self.setStyleSheet(stylesheet())
        self.setWindowIcon(app_icon())
        if self.tray is not None:
            self.tray.setIcon(tray_icon())
        # Icons are baked pixmaps, so the pages have to be rebuilt for the new
        # colour to reach them. It is a rare action, so the cost is fine.
        row = self.nav.currentRow()
        self.rebuild_devices()
        if 0 <= row < self.nav.count():
            self.nav.setCurrentRow(row)
        self._toast(tr("Saved"))

    def _on_settings_changed(self) -> None:
        self.settings.save()
        self.dashboard.graph.window_seconds = self.settings.history_seconds
        self._toast(tr("Saved"))

    def _toast(self, text: str) -> None:
        """Brief confirmation in the status bar - no dialog, no interruption."""
        self.saved_label.setText(text)
        self.saved_label.setVisible(True)
        QTimer.singleShot(1800, lambda: self.saved_label.setVisible(False))

    # ------------------------------------------------------------------
    def _schedule_save(self) -> None:
        self._save_timer.start()

    def _save_now(self) -> None:
        try:
            self.store.save()
        except OSError as exc:
            self.status_label.setText(str(exc))
            return
        self.settings.active_profile = self.store.active_name
        self.settings.save()
        self._toast(tr("Saved"))

    # ------------------------------------------------------------------
    def _on_snapshot(self, snapshot: Snapshot) -> None:
        self.dashboard.update_from(snapshot)
        current = self.nav.currentRow()
        for device in snapshot.devices:
            page = self.device_pages.get(device.key)
            if page is not None:
                page.update_from(device)
            # A device that dropped off the bus should be visible in the
            # navigation, not only once its page is open.
            item = self._nav_items.get(device.key)
            if item is not None:
                glyph = item.data(Qt.ItemDataRole.UserRole) or "device"
                row = self.nav.row(item)
                if device.error:
                    colour = PALETTE.bad
                elif row == current:
                    colour = PALETTE.accent
                else:
                    colour = PALETTE.text_dim
                item.setIcon(glyph_icon(glyph, colour, 18))

        if snapshot.emergency:
            self.status_label.setText(tr("Emergency: maximum cooling"))
            self.status_label.setStyleSheet(f"color: {PALETTE.bad}; font-weight: 600;")
        elif self.engine.paused:
            self.status_label.setText(tr("Control paused"))
            self.status_label.setStyleSheet(f"color: {PALETTE.warn};")
        elif snapshot.messages:
            self.status_label.setText(snapshot.messages[0])
            self.status_label.setStyleSheet(f"color: {PALETTE.warn};")
        else:
            hottest = self.engine.sensors.hottest()
            text = tr("Connected")
            if hottest is not None:
                text = f"{text} · {hottest:.1f} °C"
            self.status_label.setText(text)
            self.status_label.setStyleSheet(f"color: {PALETTE.text_dim};")

        if self.tray is not None:
            hottest = self.engine.sensors.hottest()
            tip = f"{APP_NAME} · {self.store.active_name}"
            if hottest is not None:
                tip += f" · {hottest:.0f} °C"
            self.tray.setToolTip(tip)

    # ------------------------------------------------------------------
    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        self._save_now()
        if self.tray is not None:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.settings.close_to_tray and self.tray is not None:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                APP_NAME,
                tr("Still running in the background - fan control stays active."),
                tray_icon(),
                2500,
            )
            return
        self._save_now()
        event.accept()
