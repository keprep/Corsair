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
    parser.add_argument(
        "--protocols",
        action="store_true",
        help="list the protocols an unrecognised device can be bound to",
    )
    parser.add_argument(
        "--try-bind",
        metavar="ID=PROTOCOL",
        help=(
            "test-drive a forced binding, e.g. 1b1c:0c40=commander-core-aio, "
            "and print what the device answered without saving anything"
        ),
    )
    parser.add_argument(
        "--bind",
        metavar="ID=PROTOCOL",
        help="save a forced binding so the device is used from now on",
    )
    parser.add_argument(
        "--unbind", metavar="ID", help="remove a saved binding, e.g. 1b1c:0c40"
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
        if device.supports_screen:
            channels = ", ".join(c.channel_id for c in device.screen_channels)
            print(f"  screen     : {channels}")
        if device.is_hwmon:
            controllable = sum(1 for c in device.channels if c.controllable)
            print(f"  writable   : {controllable}/{len(device.channels)} channels")
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


def print_protocols() -> int:
    from corsair_control.core.experimental import PROTOCOLS

    print("Protocols an unrecognised device can be forced onto:\n")
    for protocol in PROTOCOLS.values():
        print(f"  {protocol.key:<22} {protocol.label}")
        if protocol.note:
            print(f"  {'':<22} matches: {protocol.note}")
    print(
        "\nUse it like:  corsair-control --try-bind 1b1c:0c40=commander-core-aio"
        "\nThis writes to the device. Only point it at cooling hardware."
    )
    return 0


def try_binding(spec: str) -> int:
    from corsair_control.core.experimental import Binding, probe

    try:
        binding = Binding.parse(spec)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    print(f"Trying {binding} …")
    backend, outcome = probe(binding)
    if backend is None:
        print(f"  no: {outcome.describe()}")
        print(
            "\nEither the device speaks a different protocol, or it is not cooling "
            "hardware at all. Try another protocol from --protocols."
        )
        return 1

    print(f"  yes: {outcome.describe()}\n")
    for entry in outcome.status:
        key, value = entry[0], entry[1]
        unit = entry[2] if len(entry) > 2 else ""
        print(f"    {key:<28} {value} {unit}".rstrip())
    try:
        backend.disconnect()
    except Exception:  # pragma: no cover - best effort
        pass

    print(
        f"\nLooks usable. Keep it with:  corsair-control --bind {binding}"
        "\nCheck the readings above against what the hardware is really doing "
        "before you let it control anything."
    )
    return 0


def save_binding(settings: Settings, spec: str) -> int:
    from corsair_control.core.experimental import Binding

    try:
        binding = Binding.parse(spec)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    kept = [b for b in settings.experimental_bindings if not str(b).startswith(binding.usb_id)]
    kept.append(str(binding))
    settings.experimental_bindings = kept
    settings.save()
    print(f"Saved: {binding}")
    print("It is used on the next start. Remove it again with --unbind.")
    return 0


def remove_binding(settings: Settings, usb_id: str) -> int:
    prefix = usb_id.strip().lower()
    remaining = [b for b in settings.experimental_bindings if not str(b).lower().startswith(prefix)]
    if len(remaining) == len(settings.experimental_bindings):
        print(f"No saved binding starts with '{usb_id}'.", file=sys.stderr)
        return 1
    settings.experimental_bindings = remaining
    settings.save()
    print(f"Removed the binding for {usb_id}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    ensure_dirs()

    if args.protocols:
        return print_protocols()
    if args.try_bind:
        return try_binding(args.try_bind)

    settings = Settings.load()
    if args.bind:
        return save_binding(settings, args.bind)
    if args.unbind:
        return remove_binding(settings, args.unbind)

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
    from corsair_control.ui.theme import dark_palette, set_accent

    set_language(args.lang or settings.extra.get("language", "system"))
    set_accent(settings.accent)

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
