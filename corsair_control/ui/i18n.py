"""A deliberately tiny translation layer.

Source strings are English; German is shipped because that is the audience the
project started with. Anything without a translation falls back to the source
string, so adding a language never breaks the UI.
"""

from __future__ import annotations

import os

GERMAN: dict[str, str] = {
    # window / navigation
    "Dashboard": "Übersicht",
    "Devices": "Geräte",
    "Lighting": "Beleuchtung",
    "Settings": "Einstellungen",
    "Profile": "Profil",
    "New profile": "Neues Profil",
    "Rename profile": "Profil umbenennen",
    "Delete profile": "Profil löschen",
    "Duplicate profile": "Profil duplizieren",
    "Profile name": "Profilname",
    "Save": "Speichern",
    "Saved": "Gespeichert",
    "Rescan devices": "Geräte neu suchen",
    "Quit": "Beenden",
    "Show window": "Fenster anzeigen",
    "About": "Über",
    # dashboard
    "Temperatures": "Temperaturen",
    "Fans and pumps": "Lüfter und Pumpen",
    "History": "Verlauf",
    "No devices found": "Keine Geräte gefunden",
    "Sensor": "Sensor",
    "Speed": "Drehzahl",
    "Duty": "Leistung",
    "Target": "Ziel",
    "Temperature": "Temperatur",
    "Channel": "Kanal",
    "Device": "Gerät",
    # channel card
    "Mode": "Modus",
    "Curve": "Kurve",
    "Fixed": "Fest",
    "Manual": "Nicht steuern",
    "Temperature source": "Temperaturquelle",
    "Edit curve": "Kurve bearbeiten",
    "Minimum": "Minimum",
    "Maximum": "Maximum",
    "Allow zero RPM": "0 U/min erlauben",
    "Run curve on the device": "Kurve auf dem Gerät ausführen",
    "The curve is written to the controller once and keeps running "
    "even when this application is not.":
        "Die Kurve wird einmal auf den Controller geschrieben und läuft dort "
        "weiter, auch wenn dieses Programm nicht läuft.",
    "Applied": "Angewendet",
    "Switch this channel to curve mode to edit it.":
        "Diesen Kanal auf den Modus „Kurve“ stellen, um ihn zu bearbeiten.",
    "Still running in the background - fan control stays active.":
        "Läuft im Hintergrund weiter – die Lüftersteuerung bleibt aktiv.",
    "Pump": "Pumpe",
    "Fan": "Lüfter",
    "not controllable": "nicht steuerbar",
    # curve editor
    "Curve editor": "Kurven-Editor",
    "Drag points, double-click to add, right-click to remove.":
        "Punkte ziehen, Doppelklick zum Hinzufügen, Rechtsklick zum Entfernen.",
    "Preset": "Vorlage",
    "Apply preset": "Vorlage anwenden",
    "Reset": "Zurücksetzen",
    "Silent": "Leise",
    "Balanced": "Ausgewogen",
    "Performance": "Leistung",
    "Extreme": "Extrem",
    # lighting
    "Colour": "Farbe",
    "Pick colour": "Farbe wählen",
    "Apply": "Anwenden",
    "This device has no controllable lighting.":
        "Dieses Gerät hat keine steuerbare Beleuchtung.",
    "Lighting mode": "Beleuchtungsmodus",
    # settings
    "Polling interval (seconds)": "Abfrageintervall (Sekunden)",
    "Emergency temperature (°C)": "Notfalltemperatur (°C)",
    "Apply profile on start": "Profil beim Start anwenden",
    "Start minimised to tray": "Minimiert in den Infobereich starten",
    "Close to tray": "Beim Schließen in den Infobereich",
    "History length (seconds)": "Verlaufslänge (Sekunden)",
    "Language": "Sprache",
    "Restart required for language changes.":
        "Sprachänderungen wirken nach einem Neustart.",
    "System": "System",
    "German": "Deutsch",
    "English": "Englisch",
    "Safety": "Sicherheit",
    "Above this temperature every channel is forced to 100 %.":
        "Oberhalb dieser Temperatur werden alle Kanäle auf 100 % gezwungen.",
    # status / errors
    "Connected": "Verbunden",
    "Demo mode - no hardware is being controlled":
        "Demo-Modus – es wird keine Hardware angesteuert",
    "Emergency: maximum cooling": "Notfall: maximale Kühlung",
    "Control paused": "Steuerung pausiert",
    "Pause control": "Steuerung pausieren",
    "Resume control": "Steuerung fortsetzen",
    "Permission denied. Install the udev rules and replug the device.":
        "Keine Berechtigung. udev-Regeln installieren und Gerät neu anstecken.",
    "Troubleshooting": "Fehlerbehebung",
    "Copy details": "Details kopieren",
    "No supported Corsair device was found.":
        "Es wurde kein unterstütztes Corsair-Gerät gefunden.",
    "Start the application with --demo to explore the interface.":
        "Starte die Anwendung mit --demo, um die Oberfläche auszuprobieren.",
}

_LANGUAGES = {"de": GERMAN, "en": {}}
_active: dict[str, str] = {}


def detect_language() -> str:
    for var in ("CORSAIR_CONTROL_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            code = value.split(".")[0].split("_")[0].lower()
            if code in _LANGUAGES:
                return code
    return "en"


def set_language(code: str) -> None:
    global _active
    if code == "system":
        code = detect_language()
    _active = _LANGUAGES.get(code, {})


def tr(text: str) -> str:
    return _active.get(text, text)


set_language("system")
