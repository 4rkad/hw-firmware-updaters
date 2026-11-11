#!/usr/bin/env bash
# Instala los lanzadores de escritorio y los entornos virtuales de cada app.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
ICONS="$DATA/icons/hicolor/512x512/apps"
APPS="$DATA/applications"

usage() {
    echo "Uso: $0 [--uninstall]"
}

install_one() {
    local dir="$1" key="$2" label="$3" comment="$4"
    echo "· $label"
    if [ ! -d "$ROOT/$dir/.venv" ]; then
        python3 -m venv "$ROOT/$dir/.venv"
        "$ROOT/$dir/.venv/bin/pip" install -q -r "$ROOT/$dir/requirements.txt"
    fi
    install -Dm644 "$ROOT/icons/$key.png" "$ICONS/hwfw-$key.png"
    cat > "$APPS/hwfw-$key.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$label
Comment=$comment
Exec=$ROOT/$dir/run.sh
TryExec=$ROOT/$dir/run.sh
Icon=hwfw-$key
Terminal=false
Categories=Utility;
Keywords=firmware;bitcoin;hardware;
StartupNotify=true
EOF
    chmod 755 "$APPS/hwfw-$key.desktop"
}

uninstall_one() {
    local key="$1"
    rm -f "$APPS/hwfw-$key.desktop" "$ICONS/hwfw-$key.png"
}

mkdir -p "$APPS"

if [ "${1:-}" = "--uninstall" ]; then
    for key in trezor ledger bitbox; do
        uninstall_one "$key"
    done
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true
    echo "Lanzadores eliminados."
    exit 0
fi

[ "${1:-}" = "--help" ] && { usage; exit 0; }

install_one trezor trezor "Actualizador de firmware · Trezor (no oficial)" \
    "Actualiza el firmware del Trezor sin Trezor Suite ni telemetría"
install_one ledger ledger "Actualizador de firmware · Ledger (no oficial)" \
    "Actualiza firmware, apps e idioma del Ledger sin Ledger Wallet"
install_one bitbox bitbox "Actualizador de firmware · BitBox (no oficial)" \
    "Actualiza el firmware de la BitBox sin BitBoxApp ni telemetría"

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true
echo "Listo. Las apps aparecen en el menú de aplicaciones."
