"""Headless control daemon.

Runs the same engine as the GUI without Qt, so fan curves keep working before
anyone logs in and after the window is closed. It watches the profile file and
picks up changes made by the GUI within a few seconds.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from corsair_control.core.config import Settings, ensure_dirs
from corsair_control.core.engine import ControlEngine
from corsair_control.core.profile import ProfileStore, default_store
from corsair_control.version import APP_NAME, __version__

log = logging.getLogger("corsair_control.daemon")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corsair-controld",
        description=f"{APP_NAME} background service",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--profile", metavar="NAME", help="profile to activate")
    parser.add_argument("--demo", action="store_true", help="simulated devices")
    parser.add_argument(
        "--no-mainboard-fans",
        action="store_true",
        help="skip the mainboard fan headers exposed through hwmon",
    )
    parser.add_argument("--dry-run", action="store_true", help="never write duty cycles")
    parser.add_argument(
        "--restore-on-exit",
        action="store_true",
        help="set every channel to a safe speed before quitting",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser


class ProfileWatcher(threading.Thread):
    """Reloads the profile file when its modification time changes."""

    def __init__(self, engine: ControlEngine, store: ProfileStore, interval: float = 3.0) -> None:
        super().__init__(name="profile-watcher", daemon=True)
        self.engine = engine
        self.store = store
        self.interval = interval
        self._stop = threading.Event()
        self._mtime = self._current_mtime()

    def _current_mtime(self) -> float:
        try:
            return self.store.path.stat().st_mtime
        except OSError:
            return 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            mtime = self._current_mtime()
            if mtime and mtime != self._mtime:
                self._mtime = mtime
                log.info("Profile file changed, reloading")
                try:
                    self.store.load()
                except Exception:  # pragma: no cover - defensive
                    log.exception("Reload failed")
                    continue
                self.engine.config_changed()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    ensure_dirs()

    settings = Settings.load()
    store = default_store()
    if args.profile:
        store.activate(args.profile)
    elif settings.active_profile in store.profiles:
        store.activate(settings.active_profile)

    if args.no_mainboard_fans:
        settings.control_mainboard_fans = False

    engine = ControlEngine(settings, store, demo=args.demo, dry_run=args.dry_run)
    for problem in engine.discover():
        log.warning("%s", problem)

    if not engine.devices:
        log.error("No controllable device found - exiting")
        return 1

    log.info(
        "Controlling %d device(s) with profile '%s'", len(engine.devices), store.active_name
    )
    engine.start()

    watcher = ProfileWatcher(engine, store)
    watcher.start()

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    try:
        stop.wait()
    finally:
        watcher.stop()
        engine.stop(restore=args.restore_on_exit)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
