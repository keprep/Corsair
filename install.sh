#!/usr/bin/env bash
#
# Corsair Control installer.
#
#   ./install.sh              install for the current user (~/.local)
#   ./install.sh --system     install to /usr/local for every user
#   ./install.sh --daemon     additionally enable the background service
#   ./install.sh --uninstall  remove what this script installed
#
# The script never touches your profiles in ~/.config/corsair-control.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="user"
WITH_DAEMON=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --system) MODE="system" ;;
        --daemon) WITH_DAEMON=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

if [[ "$MODE" == "system" || "$WITH_DAEMON" == 1 ]]; then
    PREFIX="/usr/local"
    VENV="/opt/corsair-control"
    DESKTOP_DIR="/usr/share/applications"
    ICON_DIR="/usr/share/icons/hicolor/scalable/apps"
    SUDO="sudo"
else
    PREFIX="$HOME/.local"
    VENV="$HOME/.local/share/corsair-control/venv"
    DESKTOP_DIR="$HOME/.local/share/applications"
    ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
    SUDO=""
fi

info() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }

if [[ "$UNINSTALL" == 1 ]]; then
    info "Removing Corsair Control"
    if [[ "$WITH_DAEMON" == 1 || -f /etc/systemd/system/corsair-controld.service ]]; then
        sudo systemctl disable --now corsair-controld.service 2>/dev/null || true
        sudo rm -f /etc/systemd/system/corsair-controld.service
        sudo systemctl daemon-reload || true
    fi
    $SUDO rm -f "$PREFIX/bin/corsair-control" "$PREFIX/bin/corsair-controld"
    $SUDO rm -f "$DESKTOP_DIR/corsair-control.desktop"
    $SUDO rm -f "$ICON_DIR/corsair-control.svg"
    $SUDO rm -rf "$VENV"
    sudo rm -f /etc/udev/rules.d/60-corsair-control.rules 2>/dev/null || true
    info "Done. Your profiles were left untouched."
    exit 0
fi

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

info "Creating virtual environment in $VENV"
$SUDO mkdir -p "$(dirname "$VENV")"
$SUDO python3 -m venv "$VENV"
$SUDO "$VENV/bin/pip" install --quiet --upgrade pip
info "Installing Corsair Control and its dependencies"
$SUDO "$VENV/bin/pip" install --quiet "$REPO_DIR"

info "Linking launchers into $PREFIX/bin"
$SUDO mkdir -p "$PREFIX/bin"
$SUDO ln -sf "$VENV/bin/corsair-control" "$PREFIX/bin/corsair-control"
$SUDO ln -sf "$VENV/bin/corsair-controld" "$PREFIX/bin/corsair-controld"

info "Installing desktop entry and icon"
$SUDO mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
$SUDO install -m 644 "$REPO_DIR/packaging/corsair-control.desktop" "$DESKTOP_DIR/"
$SUDO install -m 644 "$REPO_DIR/packaging/corsair-control.svg" "$ICON_DIR/"
command -v update-desktop-database >/dev/null && \
    $SUDO update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

info "Installing udev rules (requires sudo)"
sudo install -m 644 "$REPO_DIR/packaging/60-corsair-control.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --subsystem-match=hidraw || true

if [[ "$WITH_DAEMON" == 1 ]]; then
    info "Installing the background service"
    sudo mkdir -p /etc/corsair-control /var/lib/corsair-control
    if [[ -f "$HOME/.config/corsair-control/profiles.json" && ! -f /etc/corsair-control/profiles.json ]]; then
        sudo cp "$HOME/.config/corsair-control/profiles.json" /etc/corsair-control/profiles.json
    fi
    sudo install -m 644 "$REPO_DIR/packaging/corsair-controld.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now corsair-controld.service
    info "Service status: $(systemctl is-active corsair-controld.service)"
fi

cat <<EOF

Installation finished.

  Start the GUI:      corsair-control
  Text-mode listing:  corsair-control --list
  Try it without hardware: corsair-control --demo

If no device shows up, unplug and replug it once so the new udev rules apply.
EOF
