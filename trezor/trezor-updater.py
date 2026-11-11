#!/usr/bin/env python3
"""Trezor Firmware Updater — actualiza el firmware de tu Trezor sin Trezor Suite.

No lee la seed ni ninguna xpub: la actualización solo habla con el bootloader.
Incluye gestión del dispositivo: passphrase on/off, PIN (crear/cambiar/quitar),
crear/borrar wallet, restaurar desde seed (device.recover — en Safe 3/Safe 5
las palabras se escriben en la pantalla del dispositivo), ver backup
(device.backup — re-muestra la seed) y comprobar si el dispositivo es genuino.

Uso:
    pip install -U trezor
    python trezor-updater.py            (abre la interfaz gráfica)
    python trezor-updater.py --selftest --model T3T1   (verifica firma sin dispositivo)

Binario portable:
    pyinstaller --onefile --windowed trezor-updater.py
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
import traceback
from importlib.metadata import version as pkg_version

DATA_BASE = "https://data.trezor.io/"

try:
    from trezorlib import device, firmware, messages, transport
    from trezorlib.cli import TrezorConnection
    from trezorlib.exceptions import Cancelled
except ImportError:
    print("Falta trezorlib. Instala con:  pip install -U trezor")
    sys.exit(1)

PIN_DESCRIPTIONS = {
    messages.PinMatrixRequestType.Current: "Introduce tu PIN actual",
    messages.PinMatrixRequestType.NewFirst: "Introduce el PIN nuevo",
    messages.PinMatrixRequestType.NewSecond: "Repite el PIN nuevo",
    messages.PinMatrixRequestType.WipeCodeFirst: "Introduce el código de borrado nuevo",
    messages.PinMatrixRequestType.WipeCodeSecond: "Repite el código de borrado nuevo",
}

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    HAVE_TK = True
except ImportError:
    HAVE_TK = False


def make_connection():
    try:
        return TrezorConnection(app_name="trezor-updater")
    except TypeError:
        return TrezorConnection()


def seedless_session(client):
    try:
        return client.get_session(passphrase=None)
    except TypeError:
        return client


def fetch_releases(model_internal: str) -> list[dict]:
    import requests

    url = f"{DATA_BASE}firmware/{model_internal.lower()}/releases.json"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    releases = r.json()
    if not releases:
        raise RuntimeError(f"No hay releases para {model_internal}")
    return releases


def best_release(releases, client_version, bootloader_mode, bitcoin_only, want=None):
    rels = [r for r in releases if not bitcoin_only or "url_bitcoinonly" in r]
    if not rels:
        raise RuntimeError("No hay releases bitcoin-only para este modelo")
    rels.sort(key=lambda r: r["version"], reverse=True)
    want_version = list(want) if want else rels[0]["version"]
    visited: set[tuple] = set()
    for _ in range(len(rels) + 1):
        rel = next((r for r in rels if r["version"] == want_version), None)
        if rel is None:
            higher = sorted(
                (r for r in rels if tuple(r["version"]) > tuple(want_version)),
                key=lambda r: r["version"],
            )
            if not higher:
                raise RuntimeError(
                    f"Versión {'.'.join(map(str, want_version))} no encontrada"
                )
            rel = higher[0]
            want_version = list(rel["version"])
        if client_version is None:
            return rel
        key = "min_bootloader_version" if bootloader_mode else "min_firmware_version"
        need = rel.get(key)
        if (
            not need
            or tuple(need) <= tuple(client_version)
            or tuple(need) >= tuple(want_version)
        ):
            return rel
        mark = tuple(rel["min_firmware_version"])
        if mark in visited:
            return rel
        visited.add(mark)
        visited.add(tuple(want_version))
        want_version = list(rel["min_firmware_version"])
        continue
    raise RuntimeError("No se pudo resolver una ruta de actualización válida")


def release_url(rel, bitcoin_only):
    url = rel["url_bitcoinonly" if bitcoin_only else "url"]
    return DATA_BASE + url[len("data/") :]


def release_fingerprint(rel, bitcoin_only):
    return rel.get("fingerprint_bitcoinonly" if bitcoin_only else "fingerprint")


def download(url, log):
    import requests

    log(f"Descargando {url}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def verify_firmware(data, expected_fingerprint, expected_model_names, log):
    fw = firmware.parse(data)
    fw_model = fw.model()
    if fw_model is None:
        raise RuntimeError(
            "Firmware con modelo desconocido (¿trezorlib viejo? pip install -U trezor)"
        )
    if expected_model_names and fw_model.name not in expected_model_names:
        expected = sorted(expected_model_names)[0]
        raise RuntimeError(
            f"El firmware es para {fw_model.name} pero el dispositivo es {expected}"
        )
    if isinstance(fw, firmware.VendorFirmware):
        ver = ".".join(map(str, fw.firmware.header.version[:3]))
        log(f"Imagen: {fw_model.name} firmware v{ver}, vendor '{fw.vendor_header.text}'")
    digest = fw.digest().hex()
    log(f"Fingerprint: {digest}")
    if expected_fingerprint and digest != expected_fingerprint:
        raise RuntimeError("El fingerprint NO coincide con data.trezor.io — abortando")
    try:
        fw.verify()
        log("Firma verificada contra las claves públicas de Trezor: OK")
    except firmware.Unsigned:
        raise RuntimeError("Firmware sin firma — abortando")
    except firmware.FirmwareIntegrityError:
        try:
            fw.verify(dev_keys=True)
            raise RuntimeError("Firmware firmado con claves de DESARROLLO — abortando")
        except firmware.FirmwareIntegrityError:
            raise RuntimeError("Firma de firmware INVÁLIDA — abortando")
    return fw, digest


def device_model_names(model):
    return {model.internal_name, *getattr(model, "aliases", ())}


def bitcoin_only_from_features(features):
    if features.bootloader_mode:
        return bool(features.unit_btconly)
    if messages.Capability.Bitcoin_like not in features.capabilities:
        return True
    return False


LANG_LABELS = {
    "es-ES": "Español",
    "de-DE": "Deutsch",
    "fr-FR": "Français",
    "cs-CZ": "Čeština",
    "pt-BR": "Português (Brasil)",
}


def fetch_translation_meta(model_internal, version, bitcoin_only, log):
    import requests

    ml = model_internal.lower()
    edition = "bitcoinonly" if bitcoin_only else "universal"
    url = f"{DATA_BASE}firmware/{ml}/{edition}/{ml}-{version}-{edition}.json"
    log(f"Consultando traducciones para v{version}…")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def detect_device(log):
    conn = make_connection()
    try:
        client = conn.get_client()
        f = client.features
        model = client.model
        version = tuple(client.version)
        info = {
            "name": model.name,
            "internal": model.internal_name,
            "model_names": device_model_names(model),
            "version": ".".join(map(str, version)),
            "bootloader_mode": bool(f.bootloader_mode),
            "bootloader_onev2": version[0] == 1 and version >= (1, 8, 0),
            "fw_vendor": f.fw_vendor or "-",
            "bitcoin_only": bitcoin_only_from_features(f),
            "initialized": bool(f.initialized),
            "locked": bool(getattr(f, "locked", False)),
            "language": getattr(f, "language", None),
            "pin_protection": bool(f.pin_protection),
            "passphrase_protection": bool(f.passphrase_protection),
            "backup_availability": int(
                getattr(f, "backup_availability", messages.BackupAvailability.NotAvailable)
            ),
        }
        log(
            f"Detectado {info['name']} · firmware v{info['version']} · "
            f"{'modo BOOTLOADER' if info['bootloader_mode'] else 'modo normal'}"
        )
        return info, version, info["bootloader_mode"]
    finally:
        conn.close()


def is_strict_update(client, fw):
    if not isinstance(fw, firmware.VendorFirmware):
        return False
    try:
        trust = fw.vendor_header.trust
        if not callable(getattr(trust, "is_full_trust", None)):
            return False
        if not trust.is_full_trust():
            return False
    except Exception:
        return False
    f = client.features
    return fw.vendor_header.text == f.fw_vendor and tuple(
        fw.firmware.header.version[:3]
    ) > tuple(client.version)


def header_size(fw):
    if isinstance(fw, firmware.VendorFirmware):
        return fw.firmware.header.header_len + fw.vendor_header.header_len
    return 0


def extract_embedded(data, bootloader_onev2):
    if bootloader_onev2 and data[:4] == b"TRZR" and data[256 : 256 + 4] == b"TRZF":
        return data[256:]
    return data


def flash(data, fw, bootloader_onev2, log, progress):
    conn = make_connection()
    try:
        client = conn.get_client()
        if not client.features.bootloader_mode:
            session = seedless_session(client)
            if is_strict_update(client, fw):
                log("Reiniciando a bootloader (actualización estricta)…")
                device.reboot_to_bootloader(
                    session,
                    boot_command=messages.BootCommand.INSTALL_UPGRADE,
                    firmware_header=data[: header_size(fw)],
                )
            else:
                log("Reiniciando a bootloader… confirma en el dispositivo")
                device.reboot_to_bootloader(session)
            conn.close()
            deadline = time.time() + 90
            while time.time() < deadline:
                time.sleep(0.5)
                try:
                    conn.open()
                    break
                except Exception:
                    pass
            else:
                raise RuntimeError("El dispositivo no volvió en modo bootloader (90 s)")
            client = conn.get_client()
        if not client.features.bootloader_mode:
            raise RuntimeError("El dispositivo no está en modo bootloader")
        data = extract_embedded(data, bootloader_onev2)
        total = len(data)
        log(f"Subiendo {total} bytes al bootloader…")
        sent = [0]
        state = {"last": 0}

        def cb(n):
            sent[0] += n
            if sent[0] - state["last"] >= total // 60 or sent[0] >= total:
                state["last"] = sent[0]
                progress(min(sent[0], total), total)

        firmware.update(seedless_session(client), data, cb)
        progress(total, total)
    finally:
        conn.close()


def recover_device(session, word_count, passphrase_protection, pin_protection, label, word_input, dry_run=False):
    if word_count not in (12, 18, 20, 24, 33):
        raise RuntimeError("Número de palabras no válido (12/18/20/24/33)")
    if session.features.initialized and not dry_run:
        raise RuntimeError("El dispositivo ya tiene wallet — bórrala primero")
    msg = messages.RecoveryDevice(
        word_count=word_count,
        enforce_wordlist=True,
        input_method=messages.RecoveryDeviceInputMethod.ScrambledWords,
        type=(
            messages.RecoveryType.DryRun
            if dry_run
            else messages.RecoveryType.NormalRecovery
        ),
    )
    if not dry_run:
        msg.passphrase_protection = passphrase_protection
        msg.pin_protection = pin_protection
        msg.label = label
        msg.u2f_counter = int(time.time())
    res = session.call(msg)
    while isinstance(res, messages.WordRequest):
        try:
            word = word_input(res)
            res = session.call(messages.WordAck(word=word))
        except Cancelled:
            res = session.call(messages.Cancel())
    messages.Success.ensure_isinstance(res)


class App:
    ORANGE = "#f8b21c"

    def __init__(self, root):
        self.root = root
        root.title("Trezor Firmware Updater")
        root.minsize(600, 660)
        self.setup_style()
        self.q: queue.Queue = queue.Queue()
        self.confirm_evt = threading.Event()
        self.confirm_result: list[bool] = []
        self.pin_evt = threading.Event()
        self.pin_result: list[tuple] = []
        self.word_evt = threading.Event()
        self.word_result: list[tuple] = []
        self.dialog_evt = threading.Event()
        self.dialog_result: list = []
        self.choice_evt = threading.Event()
        self.choice_result: list = []
        self.device = None
        self.release = None
        self.busy = False

        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Trezor Firmware Updater",
            font=("TkDefaultFont", 16, "bold"),
            style="Title.TLabel",
        ).pack(anchor="w", pady=(0, 14))

        card = ttk.LabelFrame(outer, text=" DISPOSITIVO ", padding=12)
        card.pack(fill="x")
        self.dev_var = tk.StringVar(
            value="Sin detectar — conecta tu Trezor"
        )
        ttk.Label(card, textvariable=self.dev_var).pack(anchor="w")
        self.rel_var = tk.StringVar(value="")
        ttk.Label(card, textvariable=self.rel_var, style="Accent.TLabel").pack(
            anchor="w", pady=(4, 0)
        )

        mgmt = ttk.LabelFrame(outer, text=" GESTIÓN DEL DISPOSITIVO ", padding=12)
        mgmt.pack(fill="x", pady=(10, 0))
        self.mgmt_var = tk.StringVar(
            value="Detecta el dispositivo para gestionar PIN, passphrase, wallet y genuinidad"
        )
        ttk.Label(mgmt, textvariable=self.mgmt_var, style="Subtitle.TLabel").pack(
            anchor="w"
        )
        mrow = ttk.Frame(mgmt)
        mrow.pack(fill="x", pady=(8, 0))
        self.btn_pass = ttk.Button(
            mrow, text="Passphrase", command=self.start_toggle_passphrase
        )
        self.btn_pass.pack(side="left")
        self.btn_pin = ttk.Button(mrow, text="PIN", command=self.start_change_pin)
        self.btn_pin.pack(side="left", padx=6)
        self.btn_pin_rm = ttk.Button(
            mrow, text="Quitar PIN", command=self.start_remove_pin
        )
        self.btn_pin_rm.pack(side="left")
        self.btn_lang = ttk.Button(
            mrow, text="Idioma", command=self.start_language
        )
        self.btn_lang.pack(side="left", padx=6)
        self.btn_genuine = ttk.Button(
            mrow, text="¿Es genuino?", command=self.start_authenticity
        )
        self.btn_genuine.pack(side="right")
        mrow2 = ttk.Frame(mgmt)
        mrow2.pack(fill="x", pady=(6, 0))
        self.btn_recover = ttk.Button(
            mrow2, text="Restaurar wallet", command=self.start_recover
        )
        self.btn_recover.pack(side="left")
        self.btn_backup = ttk.Button(
            mrow2, text="Comprobar backup", command=self.start_show_backup
        )
        self.btn_backup.pack(side="left", padx=6)
        self.btn_wipe = ttk.Button(
            mrow2, text="Borrar wallet", style="Danger.TButton", command=self.start_wipe
        )
        self.btn_wipe.pack(side="right")
        self.btn_create = ttk.Button(
            mrow2, text="Crear wallet", command=self.start_create_wallet
        )
        self.btn_create.pack(side="right", padx=6)

        self.btc_only = tk.BooleanVar(value=False)

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=14)
        self.btn_detect = ttk.Button(btns, text="Detectar", command=self.start_detect)
        self.btn_detect.pack(side="left")
        self.btn_fetch = ttk.Button(
            btns, text="Solo descargar y verificar", command=self.start_fetch_only
        )
        self.btn_fetch.pack(side="left", padx=8)
        self.btn_update = ttk.Button(
            btns,
            text="Actualizar firmware",
            style="Primary.TButton",
            command=self.start_update,
        )
        self.btn_update.pack(side="right")

        self.bar = ttk.Progressbar(
            outer, style="Sparrow.Horizontal.TProgressbar", mode="determinate"
        )
        self.bar.pack(fill="x")
        self.status = tk.StringVar(value="Listo")
        ttk.Label(outer, textvariable=self.status, style="Subtitle.TLabel").pack(
            anchor="w", pady=(5, 10)
        )

        logf = ttk.LabelFrame(outer, text=" REGISTRO ", padding=6)
        logf.pack(fill="both", expand=True)
        self.log_font = self._mono_font()
        self.log_text = tk.Text(
            logf,
            height=10,
            width=80,
            state="disabled",
            font=self.log_font,
            background="#0f0f0f",
            foreground="#b5b5b5",
            insertbackground="#d8d8d8",
            selectbackground="#4a4a4a",
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3d3d3d",
            highlightcolor="#5a5a5a",
            padx=8,
            pady=6,
        )
        scroll = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("ok", foreground="#4caf50")
        self.log_text.tag_configure("err", foreground="#ef5350")
        self.log_text.tag_configure("accent", foreground=self.ORANGE)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(
            footer,
            text=f"trezorlib {pkg_version('trezor')} · data.trezor.io",
            style="Footer.TLabel",
        ).pack(side="left")
        self.log(f"trezorlib {pkg_version('trezor')}", "accent")

        self.refresh_buttons()
        self.root.after(80, self.drain)
        self.root.after(300, self.start_detect)
        self.root.after(2000, self.watch_usb)

    def watch_usb(self):
        if not self.busy:
            try:
                found = bool(transport.enumerate_devices())
            except Exception:
                found = False
            if found and not self.device:
                self.start_detect()
            elif not found and self.device:
                self.device = None
                self.release = None
                self.rel_var.set("")
                self.dev_var.set("Sin detectar — conecta tu Trezor")
                self.mgmt_var.set(
                    "Detecta el dispositivo para gestionar PIN, passphrase, wallet y genuinidad"
                )
                self.log("Dispositivo desconectado")
                self.refresh_buttons()
        self.root.after(2000, self.watch_usb)

    def _mono_font(self):
        from tkinter import font as tkfont

        try:
            families = set(tkfont.families(self.root))
        except Exception:
            families = set()
        for fam in ("IBM Plex Mono", "JetBrains Mono", "Fira Code", "DejaVu Sans Mono"):
            if fam in families:
                return (fam, 9)
        return ("TkFixedFont", 9)

    def setup_style(self):
        from tkinter import ttk as _ttk

        bg, panel, border = "#1c1c1c", "#2a2a2a", "#3d3d3d"
        fg, dim = "#d8d8d8", "#9a9a9a"
        self.root.configure(bg=bg)
        style = _ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            ".",
            background=bg,
            foreground=fg,
            bordercolor=border,
            lightcolor=panel,
            darkcolor=panel,
            troughcolor="#111111",
            focuscolor=self.ORANGE,
            selectbackground="#4a4a4a",
            selectforeground="#ffffff",
            fieldbackground=panel,
        )
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, bordercolor=border, relief="solid")
        style.configure(
            "TLabelframe.Label",
            background=bg,
            foreground=dim,
            font=("TkDefaultFont", 9, "bold"),
        )
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Title.TLabel", foreground=self.ORANGE)
        style.configure("Subtitle.TLabel", background=bg, foreground=dim)
        style.configure("Accent.TLabel", background=bg, foreground=self.ORANGE)
        style.configure("Footer.TLabel", background=bg, foreground="#6f6f6f")
        style.configure(
            "TButton",
            background="#333333",
            foreground="#e0e0e0",
            bordercolor=border,
            focusthickness=1,
            padding=(12, 7),
        )
        style.map(
            "TButton",
            background=[("active", "#454545"), ("disabled", "#262626")],
            foreground=[("disabled", "#666666")],
            bordercolor=[("focus", self.ORANGE)],
        )
        style.configure(
            "Primary.TButton",
            background=self.ORANGE,
            foreground="#161616",
            font=("TkDefaultFont", 10, "bold"),
            bordercolor="#c78d0f",
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#ffcb57"), ("disabled", "#5a4a1e")],
            foreground=[("disabled", "#8a7a4a")],
        )
        style.configure(
            "Danger.TButton",
            background="#a03030",
            foreground="#f5d7d7",
            font=("TkDefaultFont", 9, "bold"),
            bordercolor="#7a2222",
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#c0392b"), ("disabled", "#3d2020")],
            foreground=[("disabled", "#7a5a5a")],
        )
        style.configure("TCheckbutton", background=bg, foreground=fg, focuscolor=bg)
        style.map(
            "TCheckbutton",
            background=[("active", bg)],
            indicatorcolor=[("selected", self.ORANGE), ("!selected", "#3a3a3a")],
        )
        style.configure("TRadiobutton", background=bg, foreground=fg, focuscolor=bg)
        style.map(
            "TRadiobutton",
            background=[("active", bg)],
            indicatorcolor=[("selected", self.ORANGE), ("!selected", "#3a3a3a")],
        )
        style.configure(
            "Sparrow.Horizontal.TProgressbar",
            background=self.ORANGE,
            troughcolor="#111111",
            bordercolor=border,
            lightcolor=self.ORANGE,
            darkcolor=self.ORANGE,
            thickness=10,
        )
        for s in ("TScrollbar", "Vertical.TScrollbar"):
            style.configure(
                s,
                background="#2a2a2a",
                troughcolor="#141414",
                bordercolor=border,
                arrowcolor=dim,
                relief="flat",
            )

    def log(self, msg, tag=None):
        self.q.put(("log", msg, tag))

    def set_busy(self, busy):
        self.q.put(("busy", busy))

    def progress(self, cur, total):
        self.q.put(("progress", cur, total))

    def can_act(self):
        return bool(self.device)

    def firmware_needed(self):
        if not self.release or not self.device or self.device["bootloader_mode"]:
            return True
        try:
            cur = tuple(int(x) for x in self.device["version"].split("."))
        except ValueError:
            return True
        return tuple(self.release["version"]) > cur

    def refresh_buttons(self):
        state = "disabled" if self.busy else "normal"
        self.btn_detect.configure(state=state)
        act = "disabled" if self.busy or not self.can_act() else "normal"
        self.btn_fetch.configure(state=act)
        self.btn_update.configure(
            state=(
                "normal"
                if not self.busy and self.can_act() and self.firmware_needed()
                else "disabled"
            )
        )
        mgmt_btns = (
            self.btn_pass, self.btn_pin, self.btn_pin_rm,
            self.btn_create, self.btn_wipe,
            self.btn_recover, self.btn_backup, self.btn_genuine,
            self.btn_lang,
        )
        dev = self.device
        if not dev or self.busy or dev["bootloader_mode"]:
            for b in mgmt_btns:
                b.configure(state="disabled")
            return
        self.btn_genuine.configure(state="normal")
        self.btn_lang.configure(state="normal")
        if dev["initialized"]:
            self.btn_pass.configure(state="normal")
            self.btn_pin.configure(
                state="normal",
                text="Cambiar PIN" if dev["pin_protection"] else "Crear PIN",
            )
            self.btn_pin_rm.configure(
                state="normal" if dev["pin_protection"] else "disabled"
            )
            self.btn_wipe.configure(state="normal")
            self.btn_create.configure(state="disabled")
            self.btn_backup.configure(state="normal")
            self.btn_recover.configure(state="disabled")
        else:
            for b in (self.btn_pass, self.btn_pin, self.btn_pin_rm, self.btn_wipe):
                b.configure(state="disabled")
            self.btn_create.configure(state="normal")
            self.btn_backup.configure(state="disabled")
            self.btn_recover.configure(state="normal")

    def refresh_release_label(self):
        if not self.release:
            return
        v = ".".join(map(str, self.release["version"]))
        if self.device and not self.firmware_needed():
            self.rel_var.set(f"Actualizado — v{v} es la última disponible")
        else:
            self.rel_var.set(f"Disponible: v{v}")
        self.refresh_buttons()

    def selected_model_internal(self):
        if self.device:
            return self.device["internal"]
        return None

    def drain(self):
        try:
            while True:
                ev = self.q.get_nowait()
                self.handle(ev)
        except queue.Empty:
            pass
        self.root.after(80, self.drain)

    def handle(self, ev):
        kind = ev[0]
        if kind == "log":
            msg, tag = ev[1], ev[2]
            if tag is None and ("Fingerprint" in msg or "Firma verificada" in msg):
                tag = "accent"
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n", tag or ())
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        elif kind == "progress":
            cur, total = ev[1], ev[2]
            self.bar.configure(maximum=max(total, 1), value=cur)
            self.status.set(f"Subiendo… {cur * 100 // max(total, 1)}%")
        elif kind == "busy":
            self.busy = ev[1]
            if self.busy:
                self.bar.configure(value=0)
            else:
                self.status.set("Listo")
            self.refresh_buttons()
        elif kind == "device":
            self.device = ev[1]
            if self.device:
                mode = "modo BOOTLOADER" if self.device["bootloader_mode"] else "modo normal"
                self.dev_var.set(
                    f"{self.device['name']} · firmware v{self.device['version']} · {mode} · "
                    f"vendor {self.device['fw_vendor']}"
                )
                self.btc_only.set(self.device["bitcoin_only"])
                if self.device["initialized"]:
                    self.mgmt_var.set(
                        f"Wallet: inicializada · PIN: "
                        f"{'activo' if self.device['pin_protection'] else 'inactivo'} · "
                        f"Passphrase: "
                        f"{'activada' if self.device['passphrase_protection'] else 'desactivada'}"
                    )
                else:
                    self.mgmt_var.set(
                        "Dispositivo sin wallet — puedes crear una nueva o restaurar una seed"
                    )
            else:
                self.dev_var.set(
                    "Sin detectar — conecta tu Trezor"
                )
                self.mgmt_var.set(
                    "Detecta el dispositivo para gestionar PIN, passphrase, wallet y genuinidad"
                )
        elif kind == "release":
            self.release = ev[1]
            self.refresh_release_label()
        elif kind == "confirm":
            ok = messagebox.askyesno("Confirmar instalación", ev[1])
            self.confirm_result.clear()
            self.confirm_result.append(ok)
            self.confirm_evt.set()
        elif kind == "pin":
            self.show_pin_dialog(ev[1])
        elif kind == "word":
            self.show_word_dialog()
        elif kind == "create":
            self.show_create_dialog()
        elif kind == "recover":
            self.show_recover_dialog()
        elif kind == "checkbackup":
            self.show_checkbackup_dialog()
        elif kind == "ask_save":
            data, suggested = ev[1], ev[2]
            path = filedialog.asksaveasfilename(
                initialfile=suggested,
                defaultextension=".bin",
                filetypes=[("Firmware", "*.bin")],
            )
            if path:
                with open(path, "wb") as fh:
                    fh.write(data)
                self.log(f"Guardado en {path}", "ok")
            else:
                self.log("No se guardó nada en disco")
        elif kind == "choice":
            _, title, msg, options = ev
            dlg = tk.Toplevel(self.root)
            dlg.title(title)
            dlg.configure(bg="#1c1c1c")
            dlg.transient(self.root)
            dlg.resizable(False, False)
            frm = ttk.Frame(dlg, padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text=msg, justify="left").pack(
                anchor="w", pady=(0, 12)
            )
            btns = ttk.Frame(frm)
            btns.pack(fill="x")

            def pick(text):
                self.choice_result.append(text)
                self.choice_evt.set()
                dlg.destroy()

            for text in options:
                ttk.Button(
                    btns, text=text, command=lambda t=text: pick(t)
                ).pack(side="left", padx=4)
            ttk.Button(btns, text="Cancelar", command=lambda: pick(None)).pack(
                side="right", padx=4
            )
            dlg.protocol("WM_DELETE_WINDOW", lambda: pick(None))
            dlg.grab_set()
            self.root.wait_window(dlg)
        elif kind == "error":
            self.log(ev[1], "err")
            self.status.set("Error")
        elif kind == "success":
            self.log(ev[1], "ok")
            self.status.set("Completado")

    def worker(self, fn):
        def run():
            try:
                fn()
            except Exception as e:
                self.q.put(("error", f"ERROR: {e}"))
                traceback.print_exc()
            finally:
                self.set_busy(False)

        threading.Thread(target=run, daemon=True).start()

    def start_detect(self):
        self.set_busy(True)
        self.release = None
        self.rel_var.set("")
        self.log("Buscando dispositivo…")
        self.worker(self.detect_job)

    def detect_job(self):
        try:
            info, version, bootloader = detect_device(lambda m: self.log(m))
        except Exception as e:
            self.q.put(("device", None))
            if "No Trezor device found" in str(e):
                self.log(
                    "No se encontró ningún Trezor. En Linux puede que falten reglas udev: "
                    "https://docs.trezor.io/TrezorSuite/udev.html"
                )
            raise
        self.q.put(("device", info))
        releases = fetch_releases(info["internal"])
        rel = best_release(releases, version, bootloader, info["bitcoin_only"])
        self.q.put(("release", rel))

    def start_fetch_only(self):
        self.set_busy(True)
        internal = self.selected_model_internal() or "?"
        self.log(f"Descargando y verificando firmware para {internal}…")
        self.worker(self.fetch_only_job)

    def fetch_only_job(self):
        internal = self.selected_model_internal()
        if not internal:
            raise RuntimeError("Detecta el dispositivo primero")
        btc = self.btc_only.get()
        releases = fetch_releases(internal)
        rel = best_release(releases, None, False, btc)
        data = download(release_url(rel, btc), lambda m: self.log(m))
        verify_firmware(
            data, release_fingerprint(rel, btc), set(), lambda m: self.log(m)
        )
        suggested = rel["url_bitcoinonly" if btc else "url"].rsplit("/", 1)[-1]
        self.q.put(("ask_save", data, suggested))
        self.q.put(("success", "Descarga y verificación completadas"))

    def start_update(self):
        self.set_busy(True)
        self.release = None
        self.rel_var.set("")
        self.log("Iniciando actualización…")
        self.worker(self.update_job)

    def update_job(self):
        info, version, bootloader = detect_device(lambda m: self.log(m))
        self.q.put(("device", info))
        btc = self.btc_only.get()
        releases = fetch_releases(info["internal"])
        rel = best_release(releases, version, bootloader, btc)
        self.q.put(("release", rel))
        if not bootloader and tuple(rel["version"]) <= version:
            self.log("El dispositivo ya tiene la última versión — nada que instalar")
            return
        data = download(release_url(rel, btc), lambda m: self.log(m))
        fw, digest = verify_firmware(
            data, release_fingerprint(rel, btc), info["model_names"], lambda m: self.log(m)
        )
        new_v = ".".join(map(str, rel["version"]))
        msg = (
            f"Se instalará el firmware v{new_v} sobre v{info['version']}.\n"
            f"Modelo: {info['name']}  ({'solo-Bitcoin' if btc else 'universal'})\n"
            f"Fingerprint:\n{digest}\n\n"
            "No desconectes el dispositivo durante la subida. ¿Continuar?"
        )
        self.q.put(("confirm", msg))
        self.confirm_evt.wait()
        if not self.confirm_result or not self.confirm_result[0]:
            self.log("Cancelado por el usuario")
            return
        flash(
            data,
            fw,
            info["bootloader_onev2"],
            lambda m: self.log(m),
            lambda c, t: self.progress(c, t),
        )
        self.q.put(
            ("success", "Actualización completada. El dispositivo se reinició solo.")
        )
        time.sleep(3)
        self.detect_job()

    # ---- Gestión del dispositivo (passphrase, PIN, wallet) ----

    BACKUP_OPTIONS = {
        "slip39-20": ("slip39", 128, messages.BackupType.Slip39_Single_Extendable),
        "slip39-33": ("slip39", 256, messages.BackupType.Slip39_Single_Extendable),
        "slip39-multi": ("slip39", 128, messages.BackupType.Slip39_Basic_Extendable),
        "slip39-grupos": ("slip39", 128, messages.BackupType.Slip39_Advanced_Extendable),
        "bip39-12": ("bip39", 128, messages.BackupType.Bip39),
        "bip39-24": ("bip39", 256, messages.BackupType.Bip39),
    }

    def gui_pin(self, request):
        code = getattr(request, "type", None)
        self.pin_evt.clear()
        self.pin_result.clear()
        self.q.put(("pin", PIN_DESCRIPTIONS.get(code, "Introduce el PIN")))
        self.pin_evt.wait()
        if not self.pin_result or self.pin_result[0] == "cancel":
            raise Cancelled("Entrada de PIN cancelada")
        return self.pin_result[1]

    def ask_choice(self, title, msg, options):
        """Diálogo modal con botones; devuelve el texto elegido o None."""
        self.choice_evt.clear()
        self.choice_result.clear()
        self.q.put(("choice", title, msg, options))
        self.choice_evt.wait()
        return self.choice_result[0] if self.choice_result else None

    def gui_button(self, br):
        code = getattr(br, "code", None)
        if code == messages.ButtonRequestType.PassphraseEntry:
            self.log("Introduce la passphrase en el dispositivo…")
        elif code == messages.ButtonRequestType.PinEntry:
            self.log("Introduce el PIN en el dispositivo…")
        else:
            self.log("Confirma en la pantalla del dispositivo…")

    def managed_client(self, conn):
        client = conn.get_client()
        app = getattr(client, "app", None)
        if app is not None:
            app.pin_callback = self.gui_pin
            app.button_callback = self.gui_button
        return client

    def _center_dialog(self, dlg):
        dlg.update_idletasks()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(f"+{rx + (rw - w) // 2}+{ry + (rh - h) // 2}")

    def show_pin_dialog(self, title):
        dlg = tk.Toplevel(self.root)
        dlg.title("PIN del Trezor")
        dlg.configure(bg="#1c1c1c")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        self._center_dialog(dlg)
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm, text=title, font=("TkDefaultFont", 11, "bold"),
            style="Accent.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            frm,
            text=(
                "Mira la matriz de dígitos en la pantalla del Trezor y\n"
                "escribe las POSICIONES de tu PIN con el teclado numérico:\n\n"
                "        7  8  9\n"
                "        4  5  6\n"
                "        1  2  3"
            ),
            style="Subtitle.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        var = tk.StringVar()
        ent = ttk.Entry(
            frm, textvariable=var, show="•", width=14, justify="center",
            font=(self.log_font[0], 14),
        )
        ent.pack(fill="x")
        ent.focus_set()

        def finish(res):
            self.pin_result.append(res)
            self.pin_evt.set()
            dlg.destroy()

        def ok(_e=None):
            pin = var.get().strip()
            if not pin or any(c not in "123456789" for c in pin):
                messagebox.showerror(
                    "PIN inválido",
                    "Escribe solo dígitos del 1 al 9 (posiciones de la matriz)",
                    parent=dlg,
                )
                return
            finish(("ok", pin))

        def cancel():
            finish(("cancel",))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancelar", command=cancel).pack(side="right")
        ttk.Button(
            btns, text="Aceptar", style="Primary.TButton", command=ok
        ).pack(side="right", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        ent.bind("<Return>", ok)
        dlg.grab_set()
        self.root.wait_window(dlg)

    def show_create_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Crear wallet")
        dlg.configure(bg="#1c1c1c")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        self._center_dialog(dlg)
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm, text="Crear wallet nueva", font=("TkDefaultFont", 11, "bold"),
            style="Accent.TLabel",
        ).pack(anchor="w")
        body = ttk.Frame(frm)
        body.pack(fill="x", pady=(10, 0))
        ttk.Label(body, text="Nombre:").grid(row=0, column=0, sticky="w", pady=2)
        label_var = tk.StringVar(value="Trezor")
        ttk.Entry(body, textvariable=label_var, width=24).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Label(body, text="Backup:").grid(row=1, column=0, sticky="nw", pady=2)
        backup_var = tk.StringVar(value="slip39-20")
        radiof = ttk.Frame(body)
        radiof.grid(row=1, column=1, sticky="w", padx=(8, 0))
        for text, value in (
            ("20 palabras · SLIP39 (1 de 1)", "slip39-20"),
            ("33 palabras · SLIP39 (1 de 1) · 256 bits", "slip39-33"),
            ("Multishare · SLIP39 N de M", "slip39-multi"),
            ("Multishare con grupos · SLIP39 avanzado", "slip39-grupos"),
            ("12 palabras · BIP39", "bip39-12"),
            ("24 palabras · BIP39", "bip39-24"),
        ):
            ttk.Radiobutton(
                radiof, text=text, value=value, variable=backup_var
            ).pack(anchor="w")
        pin_var = tk.BooleanVar(value=True)
        pass_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Proteger con PIN", variable=pin_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Checkbutton(
            body, text="Activar passphrase", variable=pass_var
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frm,
            text=(
                "El Trezor mostrará la seed (o cada share) en pantalla para\n"
                "que la copies. En multishare, el número de shares y el umbral\n"
                "se eligen en la propia pantalla del dispositivo.\n"
                "Haz el backup y verifícalo ANTES de enviar fondos."
            ),
            style="Subtitle.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        def finish(res):
            self.dialog_result.append(res)
            self.dialog_evt.set()
            dlg.destroy()

        def ok():
            finish(
                {
                    "label": label_var.get().strip(),
                    "pin": pin_var.get(),
                    "passphrase": pass_var.get(),
                    "backup": self.BACKUP_OPTIONS[backup_var.get()],
                }
            )

        def cancel():
            finish(None)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancelar", command=cancel).pack(side="right")
        ttk.Button(
            btns, text="Crear", style="Primary.TButton", command=ok
        ).pack(side="right", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.grab_set()
        self.root.wait_window(dlg)

    def gui_word(self, request):
        rtype = getattr(request, "type", messages.WordRequestType.Plain)
        if rtype != messages.WordRequestType.Plain:
            self.log("Este modelo usa entrada por matriz, no soportada aquí — cancelando")
            raise Cancelled("Entrada por matriz no soportada")
        self.word_evt.clear()
        self.word_result.clear()
        self.q.put(("word",))
        self.word_evt.wait()
        if not self.word_result or self.word_result[0] == "cancel":
            raise Cancelled("Entrada de palabra cancelada")
        return self.word_result[1]

    def show_word_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Palabra del seed")
        dlg.configure(bg="#1c1c1c")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm, text="Palabra del seed", font=("TkDefaultFont", 11, "bold"),
            style="Accent.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            frm,
            text=(
                "El dispositivo pide la palabra por el host (modelo antiguo).\n"
                "Escribe UNA palabra, la que marque la pantalla del Trezor:"
            ),
            style="Subtitle.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(6, 12))
        var = tk.StringVar()
        ent = ttk.Entry(frm, textvariable=var, width=16, justify="center",
                        font=(self.log_font[0], 14))
        ent.pack(fill="x")
        ent.focus_set()

        def finish(res):
            self.word_result.append(res)
            self.word_evt.set()
            dlg.destroy()

        def ok(_e=None):
            word = var.get().strip().lower()
            if not word:
                return
            finish(("ok", word))

        def cancel():
            finish(("cancel",))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancelar", command=cancel).pack(side="right")
        ttk.Button(
            btns, text="Enviar", style="Primary.TButton", command=ok
        ).pack(side="right", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        ent.bind("<Return>", ok)
        dlg.grab_set()
        self._center_dialog(dlg)

    RECOVERY_WORD_OPTIONS = (
        ("20 palabras · SLIP39 (1 de 1)", "20"),
        ("12 palabras · BIP39", "12"),
        ("18 palabras · BIP39", "18"),
        ("24 palabras · BIP39", "24"),
        ("33 palabras · SLIP39 (1 de 1)", "33"),
    )

    def show_recover_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Restaurar wallet")
        dlg.configure(bg="#1c1c1c")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm, text="Restaurar wallet desde seed", font=("TkDefaultFont", 11, "bold"),
            style="Accent.TLabel",
        ).pack(anchor="w")
        body = ttk.Frame(frm)
        body.pack(fill="x", pady=(10, 0))
        ttk.Label(body, text="Nombre:").grid(row=0, column=0, sticky="w", pady=2)
        label_var = tk.StringVar(value="Trezor")
        ttk.Entry(body, textvariable=label_var, width=24).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Label(body, text="Seed:").grid(row=1, column=0, sticky="nw", pady=2)
        words_var = tk.StringVar(value="20")
        radiof = ttk.Frame(body)
        radiof.grid(row=1, column=1, sticky="w", padx=(8, 0))
        for text, value in self.RECOVERY_WORD_OPTIONS:
            ttk.Radiobutton(
                radiof, text=text, value=value, variable=words_var
            ).pack(anchor="w")
        pin_var = tk.BooleanVar(value=True)
        pass_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Proteger con PIN", variable=pin_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Checkbutton(
            body, text="Activar passphrase", variable=pass_var
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frm,
            text=(
                "En Safe 3 / Safe 5 escribes las palabras en la pantalla\n"
                "del propio dispositivo: esta app nunca las ve. Hazlo en\n"
                "privado y coteja bien cada palabra."
            ),
            style="Subtitle.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        def finish(res):
            self.dialog_result.append(res)
            self.dialog_evt.set()
            dlg.destroy()

        def ok():
            finish(
                {
                    "label": label_var.get().strip(),
                    "pin": pin_var.get(),
                    "passphrase": pass_var.get(),
                    "word_count": int(words_var.get()),
                }
            )

        def cancel():
            finish(None)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancelar", command=cancel).pack(side="right")
        ttk.Button(
            btns, text="Restaurar", style="Primary.TButton", command=ok
        ).pack(side="right", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.grab_set()
        self._center_dialog(dlg)

    def show_checkbackup_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Comprobar backup")
        dlg.configure(bg="#1c1c1c")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm, text="Comprobar backup", font=("TkDefaultFont", 11, "bold"),
            style="Accent.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            frm,
            text="¿Cuántas palabras tiene tu backup?",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(6, 8))
        words_var = tk.StringVar(value="20")
        for text, value in self.RECOVERY_WORD_OPTIONS:
            ttk.Radiobutton(
                frm, text=text, value=value, variable=words_var
            ).pack(anchor="w")

        def finish(res):
            self.dialog_result.append(res)
            self.dialog_evt.set()
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancelar", command=lambda: finish(None)).pack(
            side="right"
        )
        ttk.Button(
            btns, text="Comprobar", style="Primary.TButton",
            command=lambda: finish(int(words_var.get())),
        ).pack(side="right", padx=6)
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(None))
        dlg.grab_set()
        self._center_dialog(dlg)

    def start_toggle_passphrase(self):
        self.set_busy(True)
        self.log("Cambiando el ajuste de passphrase…")
        self.worker(self.mgmt_passphrase_job)

    def mgmt_passphrase_job(self):
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            target = not client.features.passphrase_protection
            device.apply_settings(seedless_session(client), use_passphrase=target)
            self.q.put(
                ("success", f"Passphrase {'activada' if target else 'desactivada'}")
            )
        finally:
            conn.close()
        self.detect_job()

    def start_change_pin(self):
        self.set_busy(True)
        self.log("Configuración de PIN — sigue las instrucciones del dispositivo")
        self.worker(lambda: self.mgmt_pin_job(False))

    def start_remove_pin(self):
        self.set_busy(True)
        self.log("Quitando PIN — sigue las instrucciones del dispositivo")
        self.worker(lambda: self.mgmt_pin_job(True))

    def mgmt_pin_job(self, remove):
        if remove:
            self.q.put(
                (
                    "confirm",
                    "¿Quitar el PIN del dispositivo?\n"
                    "Tendrás que confirmar con el PIN actual.",
                )
            )
            self.confirm_evt.wait()
            if not self.confirm_result or not self.confirm_result[0]:
                self.log("Cancelado por el usuario")
                return
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            device.change_pin(seedless_session(client), remove=remove)
        finally:
            conn.close()
        self.q.put(
            ("success", "PIN eliminado" if remove else "PIN configurado")
        )
        self.detect_job()

    def start_create_wallet(self):
        self.set_busy(True)
        self.log("Creando wallet…")
        self.worker(self.mgmt_create_job)

    def mgmt_create_job(self):
        self.q.put(
            (
                "confirm",
                "CREAR WALLET NUEVA.\n\n"
                "Si el dispositivo ya tiene wallet, se borrará y la seed actual\n"
                "se perderá si no tienes backup físico verificado.\n"
                "El Trezor te guiará en pantalla para crear y verificar el backup.\n\n"
                "¿Continuar?",
            )
        )
        self.confirm_evt.wait()
        if not self.confirm_result or not self.confirm_result[0]:
            self.log("Cancelado por el usuario")
            return
        self.dialog_evt.clear()
        self.dialog_result.clear()
        self.q.put(("create",))
        self.dialog_evt.wait()
        opts = self.dialog_result[0] if self.dialog_result else None
        if not opts:
            self.log("Cancelado por el usuario")
            return
        _, strength, backup_type = opts["backup"]
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            if client.features.initialized:
                device.wipe(seedless_session(client))
                self.log("Wallet anterior borrada")
                conn.close()
                conn = make_connection()
                client = self.managed_client(conn)
            device.setup(
                seedless_session(client),
                strength=strength,
                backup_type=backup_type,
                pin_protection=opts["pin"],
                passphrase_protection=opts["passphrase"],
                label=opts["label"] or None,
            )
        finally:
            conn.close()
        self.q.put(
            (
                "success",
                "Wallet creada — haz y verifica el backup ANTES de enviar fondos",
            )
        )
        self.detect_job()

    def start_recover(self):
        self.set_busy(True)
        self.log("Restauración de wallet desde seed…")
        self.worker(self.mgmt_recover_job)

    def mgmt_recover_job(self):
        self.q.put(
            (
                "confirm",
                "RESTAURAR WALLET DESDE SEED.\n\n"
                "Las palabras se introducen en la pantalla del dispositivo\n"
                "(Safe 3 / Safe 5): esta app no ve ni guarda la seed.\n"
                "Si el dispositivo ya tiene wallet, se borrará antes.\n\n"
                "¿Continuar?",
            )
        )
        self.confirm_evt.wait()
        if not self.confirm_result or not self.confirm_result[0]:
            self.log("Cancelado por el usuario")
            return
        self.dialog_evt.clear()
        self.dialog_result.clear()
        self.q.put(("recover",))
        self.dialog_evt.wait()
        opts = self.dialog_result[0] if self.dialog_result else None
        if not opts:
            self.log("Cancelado por el usuario")
            return
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            if client.features.initialized:
                device.wipe(seedless_session(client))
                self.log("Wallet anterior borrada")
                conn.close()
                conn = make_connection()
                client = self.managed_client(conn)
            self.log(
                f"Introduce las {opts['word_count']} palabras en el dispositivo…"
            )
            recover_device(
                seedless_session(client),
                word_count=opts["word_count"],
                passphrase_protection=opts["passphrase"],
                pin_protection=opts["pin"],
                label=opts["label"] or None,
                word_input=self.gui_word,
            )
        finally:
            conn.close()
        self.q.put(
            (
                "success",
                "Wallet restaurada — verifica que las direcciones coinciden "
                "con tus registros antes de enviar fondos",
            )
        )
        self.detect_job()

    def start_show_backup(self):
        self.set_busy(True)
        self.log("Comprobación de backup…")
        self.worker(self.mgmt_backup_job)

    def mgmt_backup_job(self):
        dev = self.device or {}
        pending = dev.get("backup_availability") == int(
            messages.BackupAvailability.Required
        )
        if pending:
            self.q.put(
                (
                    "confirm",
                    "COMPLETAR BACKUP PENDIENTE.\n\n"
                    "El dispositivo mostrará la seed (o el share SLIP39)\n"
                    "en su pantalla para que la copies.\n"
                    "Hazlo en privado, sin cámaras ni miradas.\n\n"
                    "¿Continuar?",
                )
            )
            self.confirm_evt.wait()
            if not self.confirm_result or not self.confirm_result[0]:
                self.log("Cancelado por el usuario")
                return
            conn = make_connection()
            try:
                client = self.managed_client(conn)
                device.backup(seedless_session(client))
            finally:
                conn.close()
            self.q.put(
                (
                    "success",
                    "Seed mostrada en el dispositivo — copia TODAS las palabras "
                    "en orden y guarda el papel",
                )
            )
            self.detect_job()
            return
        self.q.put(
            (
                "confirm",
                "COMPROBAR BACKUP.\n\n"
                "Escribirás en la pantalla del Trezor las palabras de tu papel\n"
                "y el dispositivo comprobará si coinciden con la seed.\n"
                "La seed nunca se muestra ni sale del dispositivo.\n\n"
                "¿Continuar?",
            )
        )
        self.confirm_evt.wait()
        if not self.confirm_result or not self.confirm_result[0]:
            self.log("Cancelado por el usuario")
            return
        self.dialog_evt.clear()
        self.dialog_result.clear()
        self.q.put(("checkbackup",))
        self.dialog_evt.wait()
        word_count = self.dialog_result[0] if self.dialog_result else None
        if not word_count:
            self.log("Cancelado por el usuario")
            return
        self.log(
            f"Introduce las {word_count} palabras en el dispositivo…"
        )
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            try:
                recover_device(
                    seedless_session(client),
                    word_count=word_count,
                    passphrase_protection=False,
                    pin_protection=False,
                    label=None,
                    word_input=self.gui_word,
                    dry_run=True,
                )
            except Exception as e:
                if "Recovery failed" in str(e):
                    self.q.put(
                        (
                            "error",
                            "El backup NO coincide con la seed del dispositivo — "
                            "revisa el papel (palabras y orden)",
                        )
                    )
                    return
                raise
        finally:
            conn.close()
        self.q.put(
            (
                "success",
                "Backup VERIFICADO — las palabras coinciden con la seed del dispositivo",
            )
        )
        self.detect_job()

    def start_language(self):
        self.set_busy(True)
        self.log("Cambio de idioma del dispositivo…")
        self.worker(self.mgmt_language_job)

    def mgmt_language_job(self):
        dev = self.device
        if not dev:
            raise RuntimeError("Detecta el dispositivo primero")
        meta = fetch_translation_meta(
            dev["internal"], dev["version"], dev["bitcoin_only"],
            lambda m: self.log(m),
        )
        trans = meta.get("translations") or {}
        if not trans:
            raise RuntimeError("No hay traducciones para esta versión de firmware")
        options = [
            f"{LANG_LABELS.get(code, code)} ({code})" for code in trans
        ]
        options.append("English (restaurar idioma original)")
        choice = self.ask_choice(
            "Idioma del dispositivo",
            "Elige el idioma. Confirmarás en la pantalla del Trezor:",
            options,
        )
        if choice is None:
            self.log("Cancelado por el usuario")
            return
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            if choice.startswith("English"):
                device.change_language(
                    seedless_session(client), language_data=b""
                )
                self.q.put(("success", "Idioma restaurado a inglés"))
            else:
                code = next(c for c in trans if f"({c})" in choice)
                url = DATA_BASE + trans[code]
                data = download(url, lambda m: self.log(m))
                self.log(f"Blob de traducción: {len(data)} bytes")
                device.change_language(
                    seedless_session(client), language_data=data
                )
                self.q.put(("success", f"Idioma cambiado a {choice}"))
        finally:
            conn.close()
        self.detect_job()

    def start_authenticity(self):
        self.set_busy(True)
        self.log("Verificando autenticidad del dispositivo…")
        self.worker(self.mgmt_authenticity_job)

    def mgmt_authenticity_job(self):
        import secrets

        try:
            from trezorlib import authentication as auth
            from trezorlib.exceptions import UnexpectedMessageError
        except ImportError:
            raise RuntimeError(
                "trezorlib no soporta verificación de autenticidad "
                "(actualiza: pip install -U trezor)"
            )
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            session = seedless_session(client)
            self.log("Desafiando al dispositivo (AuthenticateDevice)…")
            try:
                auth.authenticate_device(session)
            except auth.DeviceNotAuthentic:
                self.q.put(
                    (
                        "error",
                        "DISPOSITIVO NO GENUINO — la verificación criptográfica "
                        "FALLÓ. NO uses este dispositivo con fondos reales.",
                    )
                )
                return
            except UnexpectedMessageError:
                raise RuntimeError(
                    "El firmware no soporta la verificación de autenticidad — "
                    "actualiza el firmware e inténtalo de nuevo"
                )
            serial = None
            try:
                resp = device.authenticate(session, secrets.token_bytes(16))
                if resp.optiga_certificates:
                    serial = auth.Certificate(
                        resp.optiga_certificates[0]
                    ).subject_serial_number()
            except Exception:
                pass
            msg = "DISPOSITIVO GENUINO — certificado criptográfico de Trezor verificado"
            if serial:
                msg += f" · serial {serial}"
            self.q.put(("success", msg))
        finally:
            conn.close()

    def start_wipe(self):
        self.set_busy(True)
        self.log("Borrando wallet…")
        self.worker(self.mgmt_wipe_job)

    def mgmt_wipe_job(self):
        self.q.put(
            (
                "confirm",
                "BORRAR WALLET del dispositivo.\n\n"
                "La seed se elimina por completo y NO es recuperable sin tu\n"
                "backup físico. Solo continúa si tienes las palabras verificadas.\n\n"
                "¿Borrar?",
            )
        )
        self.confirm_evt.wait()
        if not self.confirm_result or not self.confirm_result[0]:
            self.log("Cancelado por el usuario")
            return
        conn = make_connection()
        try:
            client = self.managed_client(conn)
            device.wipe(seedless_session(client))
        finally:
            conn.close()
        self.q.put(
            ("success", "Wallet borrada — el dispositivo está como nuevo")
        )
        self.detect_job()


def selftest(model_internal):
    releases = fetch_releases(model_internal)
    rel = best_release(releases, None, False, False)

    def log(m):
        print(m)

    data = download(release_url(rel, False), log)
    _, digest = verify_firmware(data, release_fingerprint(rel, False), set(), log)
    print(
        f"SELFTEST OK · {model_internal} v{'.'.join(map(str, rel['version']))} · "
        f"{len(data)} bytes · fingerprint {digest}"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Trezor Firmware Updater sin Trezor Suite"
    )
    parser.add_argument(
        "--selftest", action="store_true", help="Verifica firma del último firmware sin dispositivo"
    )
    parser.add_argument(
        "--model", default="T3T1", help="Modelo para --selftest (ej. T2B1, T3T1, T1B1)"
    )
    parser.add_argument("--libversion", action="store_true", help="Muestra versiones")
    args = parser.parse_args()

    if args.libversion:
        print(f"trezor-updater + trezorlib {pkg_version('trezor')}")
        return 0
    if args.selftest:
        return selftest(args.model.upper())
    if not HAVE_TK:
        print("Falta tkinter (python3-tk en Debian/Ubuntu).")
        return 1

    root = tk.Tk()
    try:
        _icon = tk.PhotoImage(
            file=os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/trezor-updater.png")
        )
        root.iconphoto(True, _icon)
        root._icon = _icon
    except Exception:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
