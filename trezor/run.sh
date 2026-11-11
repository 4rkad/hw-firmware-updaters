#!/usr/bin/env bash
set -e
umask 077
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Primera ejecución: instalando dependencias…"
    python3 -m venv .venv
    .venv/bin/pip install --require-hashes -r requirements.txt
fi

if [ "${1:-}" = "--tk" ]; then
    APP=trezor-updater.py
else
    APP=trezor_qt.py
fi

exec .venv/bin/python "$APP" >> "${XDG_CACHE_HOME:-$HOME/.cache}/trezor-updater.log" 2>&1
