#!/usr/bin/env python3
"""Genera las capturas de documentación con un dispositivo de ejemplo.

No toca hardware: construye la ventana real de cada app e inyecta el estado
que tendría con un dispositivo conectado. Sirve solo para el README.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication(sys.argv)
APP.setStyle("Fusion")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settle(window, seconds: float = 2.0):
    """Deja terminar la detección inicial y para el sondeo periódico."""
    for attr in ("watch", "timer"):
        timer = getattr(window, attr, None)
        if timer is not None:
            timer.stop()
    end = time.time() + seconds
    while time.time() < end:
        APP.processEvents()
        if not getattr(window, "busy", False):
            break
        time.sleep(0.02)
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.02)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    APP.processEvents()


def shot(window, name: str):
    window.resize(1180, 820)
    window.show()
    for _ in range(40):
        APP.processEvents()
        time.sleep(0.02)
    window.grab().save(str(ROOT / "docs" / "screenshots" / f"{name}.png"))
    window.close()
    print("docs/screenshots/%s.png" % name)


def trezor():
    mod = load(ROOT / "trezor" / "trezor_qt.py", "trezor_qt")
    win = mod.TrezorWindow()
    settle(win)
    win.logbox.clear()
    info = {
        "name": "Trezor Safe 3",
        "internal": "T2B1",
        "model_names": {"T2B1"},
        "version": "2.11.0",
        "bootloader_mode": False,
        "bootloader_onev2": False,
        "fw_vendor": "SatoshiLabs",
        "bitcoin_only": False,
        "initialized": True,
        "locked": False,
        "language": "en",
        "pin_protection": True,
        "passphrase_protection": True,
        "backup_availability": 2,
    }
    rel = {
        "version": [2, 12, 5],
        "min_firmware_version": [2, 5, 0],
        "fingerprint": "a4c1f0e9b27d5386c0de41f7a9b3e6d28c5f0a1b7d9e3c64f2a8b5d1e0c7f394",
        "fingerprint_bitcoinonly": None,
    }
    win.append_log("Trezor Safe 3 detectado · firmware v2.11.0 · modo normal", "dim")
    win.append_log("Release más reciente: v2.12.5 · Universal", "dim")
    win.apply_detect((info, (2, 11, 0), False, rel, False))
    win.append_log("Listo para actualizar cuando tú quieras.", "ok")
    shot(win, "trezor")


def ledger():
    mod = load(ROOT / "ledger" / "ledger_qt.py", "ledger_qt")
    win = mod.MainWindow()
    settle(win)
    win.logbox.clear()
    target = 0x33000004  # Ledger Nano X
    info = {
        "se_target_id": target,
        "target_id": target,
        "se_version": "2.4.0",
        "mcu_version": "1.16",
        "is_bootloader": False,
        "flags": b"\x88",  # desbloqueado + manager permitido
    }
    apps = [
        {"name": "Bitcoin", "hash": "b1"},
        {"name": "Ethereum", "hash": "e1"},
        {"name": "Solana", "hash": "s1"},
    ]
    catalog = [
        {"versionName": "Bitcoin", "version": "2.4.2", "hash": "b2"},
        {"versionName": "Ethereum", "version": "1.14.0", "hash": "e2"},
        {"versionName": "Solana", "version": "1.9.2", "hash": "s2"},
    ]
    meta = [{"version": "2.4.0"}, {"version": "1.13.1"}, {"version": "1.9.0"}]
    plan = {
        "current": "2.4.0",
        "latest": {
            "version": "2.4.3",
            "notes": "Mejoras de seguridad y estabilidad del sistema.",
        },
        "target_id": target,
        "provider": 1,
    }
    win.load_plan = lambda: None
    win.dev_combo.clear()
    win.dev_combo.addItem("Ledger Nano X · 2.4.0")
    win.search_status.setText("Última búsqueda: 17:02:11 · 1 dispositivo")
    win.append_log("Ledger Nano X detectado · firmware 2.4.0", "dim")
    win.installed, win.catalog, win.meta = apps, catalog, meta
    win.apply_device((info, apps, []))
    win.apply_plan((plan, catalog, meta))
    win.append_log("Catálogo consultado · 3 apps actualizables", "dim")
    shot(win, "ledger")


def bitbox():
    mod = load(ROOT / "bitbox" / "bitbox_qt.py", "bitbox_qt")
    win = mod.BitBoxWindow()
    settle(win)
    win.logbox.clear()
    data = {
        "mode": "normal",
        "platform": "bitbox02",
        "edition": "btconly",
        "version": "9.27.1",
        "info": {"product_string": "BitBox02BTC"},
        "initialized": True,
        "passphrase": False,
        "name": "Bitbox",
        "target": "9.27.1",
        "release_name": "v9.27.1",
        "asset": {
            "name": "firmware-bitbox02-btconly.v9.27.1.signed.bin",
            "browser_download_url": "https://github.com/BitBoxSwiss/bitbox02-firmware/releases/download/firmware%2Fv9.27.1/firmware-bitbox02-btconly.v9.27.1.signed.bin",
        },
        "dev_hash": "2522156c870b430c2f4d1e1b8e3a2b1d0f5c4e3a2b1d0c9e8f7a6b5c4d3e2f1a",
        "release_ok": True,
        "attestation": True,
    }
    win.append_log("BitBox detectada: BitBox02 Bitcoin-only · v9.27.1 · target 9.27.1", "dim")
    win.append_log("Atestación de autenticidad: OK", "ok")
    win.apply_detect(data)
    shot(win, "bitbox")


if __name__ == "__main__":
    {"trezor": trezor, "ledger": ledger, "bitbox": bitbox}[sys.argv[1]]()
