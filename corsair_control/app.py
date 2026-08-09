"""Command line entry point for the graphical application."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from corsair_control.core.config import Settings, ensure_dirs
from corsair_control.core.engine import ControlEngine
from corsair_control.core.profile import ProfileStore, default_store
from corsair_control.version import APP_NAME, __version__

log = logging.getLogger("corsair_control")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corsair-control",
        description=f"{APP_NAME} - fan, pump and lighting control for Corsair hardware on Linux",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run against simulated devices; nothing is written to real hardware",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="talk to real devices but never write a duty cycle",
    )
    parser.add_argument("--profile", metavar="NAME", help="activate this profile on start")
    parser.add_argument(
        "--list",
        action="store_true",
        help="print detected devices, channels and sensors, then exit",
    )
    parser.add_argument(
        "--minimised", action="store_true", help="start hidden in the system tray"
    )
    parser.add_argument("--lang", choices=("system", "de", "en"), help="interface language")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more logging")
    return parser


def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def print_inventory(engine: ControlEngine) -> int:
    engine.discover()
    engine.tick()

    if engine.startup_errors:
        print("Problems:")
        for error in engine.startup_errors:
            print(f"  ! {error}")
        print()

    if not engine.devices:
        print("No devices.")
    for device in engine.devices:
        print(f"{device.description}")
        print(f"  driver     : {device.driver}")
        print(f"  bus        : {device.bus_info}")
        print(f"  key        : {device.key}")
        if device.pump_modes:
            print(f"  pump modes : {', '.join(device.pump_modes)}")
        if device.supports_lighting:
            channels = ", ".join(c.channel_id for c in device.lighting_channels)
            print(f"  lighting   : {channels}")
        for name, value in device.last_status.temperatures.items():
            print(f"  temp       : {name} = {value:.1f} °C")
        for channel in device.channels:
            rpm = device.last_status.speeds.get(channel.channel_id)
            duty = device.last_status.duties.get(channel.channel_id)
            flags = "" if channel.controllable else "  (read-only)"
            print(
                f"  channel    : {channel.channel_id:<6} {channel.kind:<5}"
                f" {'' if rpm is None else f'{rpm:.0f} rpm':>9}"
                f" {'' if duty is None else f'{duty:.0f} %':>6}{flags}"
            )
        print()

    print("Temperature sources:")
    for sensor in engine.sensors.sensors:
        value = engine.sensors.value(sensor.sensor_id)
        shown = "—" if value is None else f"{value:.1f} °C"
        print(f"  {sensor.sensor_id:<44} {shown:>9}  {sensor.label}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    ensure_dirs()

    settings = Settings.load()
    store: ProfileStore = default_store()
    if args.profile:
        store.activate(args.profile)
        settings.active_profile = args.profile
    elif settings.active_profile in store.profiles:
        store.activate(settings.active_profile)

    engine = ControlEngine(settings, store, demo=args.demo, dry_run=args.dry_run)

    if args.list:
        try:
            return print_inventory(engine)
        finally:
            engine.stop()

    return run_gui(engine, settings, store, args)


def run_gui(engine: ControlEngine, settings: Settings, store: ProfileStore, args) -> int:
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print(
            "PyQt6 is not installed. Install it with 'pip install PyQt6', "
            "or use --list for a text-mode overview.",
            file=sys.stderr,
        )
        return 2

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        if not os.environ.get("QT_QPA_PLATFORM"):
            print(
                "No graphical session found. Use --list for a text-mode overview.",
                file=sys.stderr,
            )
            return 2

    from corsair_control.ui.i18n import set_language
    from corsair_control.ui.icons import app_icon
    from corsair_control.ui.main_window import MainWindow
    from corsair_control.ui.theme import dark_palette

    set_language(args.lang or settings.extra.get("language", "system"))

    app = QApplication(sys.argv[:1])
    app.setApplicationName("corsair-control")
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(app_icon())
    # Dialogs are top-level windows, so they need the palette too.
    app.setPalette(dark_palette())
    app.setQuitOnLastWindowClosed(False)

    engine.discover()
    # Monitoring always runs - only writing to the hardware is gated, so the
    # window still shows live temperatures when the user does not want the
    # profile applied automatically.
    if not (settings.apply_on_start or args.demo):
        engine.pause(True)

    window = MainWindow(engine, settings, store, demo=args.demo)
    engine.start()

    if args.minimised or settings.start_minimised:
        window.hide()
    else:
        window.show()

    # Ctrl+C in a terminal should still terminate a Qt application.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    from PyQt6.QtCore import QTimer

    heartbeat = QTimer()
    heartbeat.start(400)
    heartbeat.timeout.connect(lambda: None)

    try:
        return app.exec()
    finally:
        engine.stop()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
