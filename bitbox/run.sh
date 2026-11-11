#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Primera ejecución: instalando dependencias…"
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

if [ "${1:-}" = "--tk" ]; then
    APP=bitbox-updater.py
else
    APP=bitbox_qt.py
fi

exec .venv/bin/python "$APP" >> "${XDG_CACHE_HOME:-$HOME/.cache}/bitbox-updater.log" 2>&1
