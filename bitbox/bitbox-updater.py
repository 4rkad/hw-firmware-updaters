#!/usr/bin/env python3
"""BitBox Firmware Updater — actualiza el firmware de tu BitBox02/Nova sin BitBoxApp.

Sin telemetría ni backends de Shift: solo GitHub releases (binarios firmados,
verificados por digest SHA-256 y magic de edición) y el bootloader del dispositivo.
Incluye gestión básica: passphrase on/off, verificar backup, mostrar seed y borrado.

Uso:
    ./arrancar.sh                          (abre la interfaz gráfica)
    python bitbox-updater.py --selftest    (verifica los 4 firmwares sin dispositivo)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
from importlib.metadata import version as pkg_version

try:
    import hid
    import semver
    from bitbox02.bitbox02 import BitBox02
    from bitbox02.bitbox02.bootloader import (
        SIGDATA_MAGIC_BITBOX02_BTCONLY,
        SIGDATA_MAGIC_BITBOX02_MULTI,
        SIGDATA_MAGIC_BITBOX02PLUS_BTCONLY,
        SIGDATA_MAGIC_BITBOX02PLUS_MULTI,
        Bootloader,
        parse_signed_firmware,
    )
    from bitbox02.communication import devices as bbdevices
    from bitbox02.communication import u2fhid
    from bitbox02.communication.bitbox_api_protocol import (
        Bitbox02Exception,
        BitBoxNoiseConfig,
    )
except ImportError:
    print("Falta bitbox02. Instala con:  pip install bitbox02")
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    HAVE_TK = True
except ImportError:
    HAVE_TK = False

GITHUB_API = "https://api.github.com/repos/BitBoxSwiss/bitbox02-firmware"
RELEASE_BY_TAG = GITHUB_API + "/releases/tags/firmware/v{}"
STEPS_RE = re.compile(
    r"When upgrading from a firmware with version v(\d+\.\d+\.\d+) or below, "
    r"you first must install and run \[?firmware v(\d+\.\d+\.\d+)"
)
DEVICE_HASH_RE = re.compile(r"- (BitBox02[^:`]*): `([0-9a-f]{64})`")
def _pairing_paths():
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return (
        os.path.join(data, "hwfw", "bitbox-pairing.json"),
        os.path.expanduser("~/.cache/bitbox-updater-pairing.json"),
    )


PAIRING_FILE, LEGACY_PAIRING_FILE = _pairing_paths()

# product_string USB -> (plataforma, edición)
NORMAL_IDS = {
    "BitBox02": ("bitbox02", "multi"),
    "BitBox02BTC": ("bitbox02", "btconly"),
    "BitBox02 Nova Multi": ("bitbox02nova", "multi"),
    "BitBox02 Nova BTC-only": ("bitbox02nova", "btconly"),
}
BOOTLOADER_IDS = {
    "bb02-bootloader": ("bitbox02", "multi"),
    "bb02btc-bootloader": ("bitbox02", "btconly"),
    "BitBox02 Nova Multi bl": ("bitbox02nova", "multi"),
    "BitBox02 Nova BTC-only bl": ("bitbox02nova", "btconly"),
}
EDITION_MAGIC = {
    ("bitbox02", "multi"): SIGDATA_MAGIC_BITBOX02_MULTI,
    ("bitbox02", "btconly"): SIGDATA_MAGIC_BITBOX02_BTCONLY,
    ("bitbox02nova", "multi"): SIGDATA_MAGIC_BITBOX02PLUS_MULTI,
    ("bitbox02nova", "btconly"): SIGDATA_MAGIC_BITBOX02PLUS_BTCONLY,
}
EDITION_LABEL = {
    ("bitbox02", "multi"): "BitBox02 Multi",
    ("bitbox02", "btconly"): "BitBox02 Bitcoin-only",
    ("bitbox02nova", "multi"): "BitBox02 Nova Multi",
    ("bitbox02nova", "btconly"): "BitBox02 Nova Bitcoin-only",
}
ASSET_RE = re.compile(
    r"firmware-(bitbox02|bitbox02nova)-(multi|btconly)\.v(\d+\.\d+\.\d+)\.signed\.bin$"
)


class PairingStore(BitBoxNoiseConfig):
    """Persiste las claves noise en ~/.cache para no re-emparejar en cada ejecución."""

    def __init__(self):
        self._data = {}
        self._app = None
        self._last_attestation = None
        self.quiet_attestation = False
        try:
            with open(PAIRING_FILE) as fh:
                self._data = json.load(fh)
        except Exception:
            # The legacy file could have been world-readable. Do not reuse it:
            # remove it and force a new pairing.
            try:
                os.remove(LEGACY_PAIRING_FILE)
            except OSError:
                pass
        self._callback_log = None

    def _save(self):
        try:
            os.makedirs(os.path.dirname(PAIRING_FILE), mode=0o700, exist_ok=True)
            tmp = PAIRING_FILE + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh)
            os.replace(tmp, PAIRING_FILE)
            os.chmod(PAIRING_FILE, 0o600)
        except Exception as exc:
            if self._app is not None:
                self._app.log(f"No se pudo guardar el emparejamiento: {exc}", "warn")

    def contains_device_static_pubkey(self, pubkey: bytes) -> bool:
        return pubkey.hex() in self._data.get("devices", {})

    def add_device_static_pubkey(self, pubkey: bytes) -> None:
        self._data.setdefault("devices", {})[pubkey.hex()] = int(time.time())
        self._save()

    def get_app_static_privkey(self):
        priv = self._data.get("app_privkey")
        return bytes.fromhex(priv) if priv else None

    def set_app_static_privkey(self, privkey: bytes) -> None:
        self._data["app_privkey"] = privkey.hex()
        self._save()

    def show_pairing(self, code: str, device_response) -> bool:
        if self._app is None:
            return device_response()
        return self._app.pairing_flow(code, device_response)

    def attestation_check(self, result: bool) -> None:
        self._last_attestation = result
        if result and self.quiet_attestation:
            return
        if self._app is not None:
            self._app.log(
                "Atestación de autenticidad: " + ("OK" if result else "FALLÓ"),
                "ok" if result else "err",
            )


def hid_transport(info):
    h = hid.device()
    h.open_path(info["path"])
    return u2fhid.U2FHid(h)


def make_client(info, noise, log, unlock_first=False):
    client = BitBox02(
        transport=hid_transport(info), device_info=info, noise_config=noise
    )
    client.check_min_version()
    if unlock_first:
        # OP_UNLOCK antes del emparejamiento, como hace la BitBoxApp:
        # el dispositivo muestra la entrada de contraseña y la query bloquea
        # hasta que el usuario termina (o aborta).
        log("Introduce la contraseña en el dispositivo (caracteres alternando, toca el tuyo)…")
        client._bitbox_protocol.unlock_query()
        log("Desbloqueado")
    return client


# ---------- GitHub ----------

def fetch_json(url):
    import requests

    r = requests.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()


_LATEST_CACHE = {"at": 0.0, "release": None, "failed_at": 0.0}


def _release_cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "bitbox-updater-release.json")


def _load_release_cache():
    try:
        with open(_release_cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("assets"):
            return data
    except Exception:
        pass
    return None


def _save_release_cache(release):
    try:
        path = _release_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(release, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def fetch_latest_release(max_age=1800, retry_after=300):
    """Último release con caché en memoria y en disco.

    GitHub limita a 60 peticiones/hora sin token: tras un fallo se espera
    `retry_after` antes de volver a intentarlo y se usa el release guardado.
    """
    now = time.time()
    release = _LATEST_CACHE["release"]
    if release is not None and now - _LATEST_CACHE["at"] < max_age:
        return release
    if now - _LATEST_CACHE["failed_at"] < retry_after:
        release = release or _load_release_cache()
        if release is not None:
            return release
        raise RuntimeError("GitHub no disponible (límite de peticiones); reintento en unos minutos")
    try:
        release = fetch_json(GITHUB_API + "/releases/latest")
    except Exception:
        _LATEST_CACHE["failed_at"] = now
        release = release or _load_release_cache()
        if release is not None:
            return release
        raise
    _LATEST_CACHE.update(at=now, release=release, failed_at=0.0)
    _save_release_cache(release)
    return release


def pick_asset(release, platform, edition):
    for asset in release.get("assets", []):
        m = ASSET_RE.search(asset["name"])
        if m and m.group(1) == platform and m.group(2) == edition:
            return asset
    return None


def download(url, log):
    import requests

    log(f"Descargando {url.rsplit('/', 1)[-1]}")
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    return r.content


def verify_blob(data, asset, platform, edition, log, require_digest=True):
    digest = hashlib.sha256(data).hexdigest()
    expected = (asset.get("digest") or "").split("sha256:", 1)[-1]
    log(f"SHA-256: {digest}")
    if not expected:
        if require_digest:
            raise RuntimeError("El release no publica SHA-256 del asset — abortando")
        log("El release no publica SHA-256 para este paso intermedio; se omite esa comprobación (la firma la valida el bootloader).", "warn")
    elif digest != expected:
        raise RuntimeError("El SHA-256 NO coincide con el de GitHub — abortando")
    magic, _sigdata, fw = parse_signed_firmware(data)
    if magic != EDITION_MAGIC[(platform, edition)]:
        raise RuntimeError(
            "El firmware es de otra edición que el dispositivo — abortando"
        )
    log(f"Imagen: {EDITION_LABEL[(platform, edition)]} · {len(fw)} bytes · contenedor firmado (firma validada por el bootloader)")
    return fw


def parse_steps(body):
    return [
        (semver.VersionInfo.parse(bound), semver.VersionInfo.parse(step))
        for bound, step in STEPS_RE.findall(body)
    ]


def upgrade_path(current, body):
    """Devuelve las versiones intermedias (ascendentes) que hay que instalar antes."""
    steps = sorted({step for bound, step in parse_steps(body) if current <= bound})
    return [f"{v.major}.{v.minor}.{v.patch}" for v in steps]


def parse_device_hashes(body):
    return dict(DEVICE_HASH_RE.findall(body))


# ---------- Dispositivo ----------

def usb_scan():
    normals = []
    for info in bbdevices.get_any_bitbox02s():
        normals.append(info)
    bootloaders = bbdevices.get_any_bitbox02_bootloaders()
    return normals, bootloaders


def wait_for(predicate, timeout, log, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            if predicate():
                return True
        except Exception:
            pass
    raise RuntimeError(f"Tiempo de espera agotado esperando: {what}")


class App:
    ORANGE = "#f8b21c"

    def __init__(self, root):
        self.root = root
        root.title("BitBox Firmware Updater")
        root.minsize(620, 680)
        self.setup_style()
        self.q: queue.Queue = queue.Queue()
        self.confirm_evt = threading.Event()
        self.confirm_result: list[bool] = []
        self.choice_evt = threading.Event()
        self.choice_result: list = []
        self.text_evt = threading.Event()
        self.text_result: list = []
        self.busy = False
        self.device = None  # {"mode","platform","edition","version","info"}
        self.release_v = None
        self.release_name = None
        self.noise = PairingStore()
        self.noise._app = self

        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text="BitBox Firmware Updater", font=("TkDefaultFont", 16, "bold"),
            style="Title.TLabel",
        ).pack(anchor="w", pady=(0, 14))

        card = ttk.LabelFrame(outer, text=" DISPOSITIVO ", padding=12)
        card.pack(fill="x")
        self.dev_var = tk.StringVar(value="Sin detectar — conecta tu BitBox")
        ttk.Label(card, textvariable=self.dev_var).pack(anchor="w")
        self.rel_var = tk.StringVar(value="")
        ttk.Label(card, textvariable=self.rel_var, style="Accent.TLabel").pack(
            anchor="w", pady=(4, 0)
        )

        mgmt = ttk.LabelFrame(outer, text=" GESTIÓN DEL DISPOSITIVO ", padding=12)
        mgmt.pack(fill="x", pady=(10, 0))
        self.mgmt_var = tk.StringVar(
            value="Detecta el dispositivo para gestionar passphrase, backup y borrado"
        )
        ttk.Label(mgmt, textvariable=self.mgmt_var, style="Subtitle.TLabel").pack(anchor="w")
        mrow = ttk.Frame(mgmt)
        mrow.pack(fill="x", pady=(8, 0))
        self.btn_pass = ttk.Button(mrow, text="Passphrase", command=self.start_toggle_passphrase)
        self.btn_pass.pack(side="left")
        self.btn_checkbak = ttk.Button(mrow, text="Verificar backup", command=self.start_check_backup)
        self.btn_checkbak.pack(side="left", padx=6)
        self.btn_showseed = ttk.Button(mrow, text="Mostrar seed", command=self.start_show_mnemonic)
        self.btn_showseed.pack(side="left")
        self.btn_wipe = ttk.Button(
            mrow, text="Borrar dispositivo", style="Danger.TButton", command=self.start_wipe
        )
        self.btn_wipe.pack(side="right")
        mrow2 = ttk.Frame(mgmt)
        mrow2.pack(fill="x", pady=(6, 0))
        self.btn_create = ttk.Button(
            mrow2, text="Crear wallet (seed nueva)", style="Primary.TButton",
            command=self.start_create_wallet,
        )
        self.btn_create.pack(side="left")
        self.btn_restore = ttk.Button(
            mrow2, text="Restaurar de microSD", command=self.start_restore_backup
        )
        self.btn_restore.pack(side="left", padx=6)
        mrow3 = ttk.Frame(mgmt)
        mrow3.pack(fill="x", pady=(6, 0))
        self.btn_name = ttk.Button(mrow3, text="Renombrar", command=self.start_rename)
        self.btn_name.pack(side="left")
        self.btn_bip85 = ttk.Button(mrow3, text="BIP85…", command=self.start_bip85)
        self.btn_bip85.pack(side="left", padx=6)
        self.btn_pwd = ttk.Button(
            mrow3, text="Cambiar contraseña", command=self.start_change_password
        )
        self.btn_pwd.pack(side="left")
        self.btn_genuine = ttk.Button(
            mrow3, text="¿Es genuino?", command=self.start_genuine
        )
        self.btn_genuine.pack(side="right")

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=14)
        self.btn_detect = ttk.Button(btns, text="Detectar", command=self.start_detect)
        self.btn_detect.pack(side="left")
        self.btn_fetch = ttk.Button(
            btns, text="Solo descargar y verificar", command=self.start_fetch_only
        )
        self.btn_fetch.pack(side="left", padx=8)
        self.btn_update = ttk.Button(
            btns, text="Actualizar firmware", style="Primary.TButton",
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
            logf, height=10, width=80, state="disabled", font=self.log_font,
            background="#0f0f0f", foreground="#b5b5b5", insertbackground="#d8d8d8",
            selectbackground="#4a4a4a", selectforeground="#ffffff", relief="flat",
            highlightthickness=1, highlightbackground="#3d3d3d", highlightcolor="#5a5a5a",
            padx=8, pady=6,
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
            text=f"bitbox02 lib {pkg_version('bitbox02')} · github.com/BitBoxSwiss/bitbox02-firmware",
            style="Footer.TLabel",
        ).pack(side="left")
        self.log(f"bitbox02 lib {pkg_version('bitbox02')}", "accent")

        self.refresh_buttons()
        self.root.after(80, self.drain)
        self.root.after(300, self.start_detect)
        self.root.after(2000, self.watch_usb)

    def watch_usb(self):
        if not self.busy:
            try:
                normals, bootloaders = usb_scan()
                found = bool(normals or bootloaders)
            except Exception:
                found = False
            if found and not self.device:
                self.start_detect()
            elif not found and self.device:
                self.device = None
                self.release_v = None
                self.rel_var.set("")
                self.dev_var.set("Sin detectar — conecta tu BitBox")
                self.mgmt_var.set(
                    "Detecta el dispositivo para gestionar passphrase, backup y borrado"
                )
                self.log("Dispositivo desconectado")
                self.refresh_buttons()
        self.root.after(2000, self.watch_usb)

    # ---------- estilo (idéntico al trezor-updater) ----------

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
        bg, panel, border = "#1c1c1c", "#2a2a2a", "#3d3d3d"
        fg, dim = "#d8d8d8", "#9a9a9a"
        self.root.configure(bg=bg)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            ".", background=bg, foreground=fg, bordercolor=border, lightcolor=panel,
            darkcolor=panel, troughcolor="#111111", focuscolor=self.ORANGE,
            selectbackground="#4a4a4a", selectforeground="#ffffff", fieldbackground=panel,
        )
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, bordercolor=border, relief="solid")
        style.configure(
            "TLabelframe.Label", background=bg, foreground=dim,
            font=("TkDefaultFont", 9, "bold"),
        )
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Title.TLabel", foreground=self.ORANGE)
        style.configure("Subtitle.TLabel", background=bg, foreground=dim)
        style.configure("Accent.TLabel", background=bg, foreground=self.ORANGE)
        style.configure("Footer.TLabel", background=bg, foreground="#6f6f6f")
        style.configure(
            "TButton", background="#333333", foreground="#e0e0e0", bordercolor=border,
            focustickness=1, padding=(12, 7),
        )
        style.map(
            "TButton", background=[("active", "#454545"), ("disabled", "#262626")],
            foreground=[("disabled", "#666666")], bordercolor=[("focus", self.ORANGE)],
        )
        style.configure(
            "Primary.TButton", background=self.ORANGE, foreground="#161616",
            font=("TkDefaultFont", 10, "bold"), bordercolor="#c78d0f",
        )
        style.map(
            "Primary.TButton", background=[("active", "#ffcb57"), ("disabled", "#5a4a1e")],
            foreground=[("disabled", "#8a7a4a")],
        )
        style.configure(
            "Danger.TButton", background="#a03030", foreground="#f5d7d7",
            font=("TkDefaultFont", 9, "bold"), bordercolor="#7a2222",
        )
        style.map(
            "Danger.TButton", background=[("active", "#c0392b"), ("disabled", "#3d2020")],
            foreground=[("disabled", "#7a5a5a")],
        )
        style.configure(
            "TCombobox", fieldbackground="#2e2e2e", background="#333333",
            foreground="#e0e0e0", arrowcolor=dim, bordercolor=border,
        )
        style.map(
            "TCombobox", fieldbackground=[("readonly", "#2e2e2e")],
            foreground=[("readonly", "#e0e0e0")],
        )
        self.root.option_add("*TCombobox*Listbox.background", "#2e2e2e")
        self.root.option_add("*TCombobox*Listbox.foreground", "#e0e0e0")
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.ORANGE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#161616")
        style.configure(
            "Sparrow.Horizontal.TProgressbar", background=self.ORANGE,
            troughcolor="#111111", bordercolor=border, lightcolor=self.ORANGE,
            darkcolor=self.ORANGE, thickness=10,
        )
        for s in ("TScrollbar", "Vertical.TScrollbar"):
            style.configure(
                s, background="#2a2a2a", troughcolor="#141414", bordercolor=border,
                arrowcolor=dim, relief="flat",
            )

    # ---------- eventos / helpers UI ----------

    def log(self, msg, tag=None):
        self.q.put(("log", msg, tag))

    def set_busy(self, busy):
        self.q.put(("busy", busy))

    def progress(self, cur, total):
        self.q.put(("progress", cur, total))

    def ask_confirm(self, msg):
        self.confirm_evt.clear()
        self.confirm_result.clear()
        self.q.put(("confirm", msg))
        self.confirm_evt.wait()
        return bool(self.confirm_result) and self.confirm_result[0]

    def ask_choice(self, title, msg, options):
        """Diálogo modal con botones; devuelve el texto elegido o None."""
        self.choice_evt.clear()
        self.choice_result.clear()
        self.q.put(("choice", title, msg, options))
        self.choice_evt.wait()
        return self.choice_result[0] if self.choice_result else None

    def ask_text(self, title, msg, initial=""):
        """Diálogo modal de entrada de texto; devuelve el texto o None."""
        self.text_evt.clear()
        self.text_result.clear()
        self.q.put(("ask_text", title, msg, initial))
        self.text_evt.wait()
        return self.text_result[0] if self.text_result else None

    def pairing_flow(self, code, device_response):
        """Muestra el código en PC y dispositivo A LA VEZ.

        device_response() invoca la pantalla de emparejamiento del BitBox y
        bloquea hasta que el usuario toca ✓ o ✗ en el dispositivo.
        """
        self.log(f"Código de emparejamiento: {code}", "accent")
        self.q.put(("pairing_show", code))
        try:
            ok = bool(device_response())
        except Exception as e:
            self.log(f"Emparejamiento interrumpido: {e}", "err")
            ok = False
        self.q.put(("pairing_close", ok))
        if ok:
            self.log("Emparejamiento confirmado en el dispositivo", "ok")
        else:
            self.log("Emparejamiento rechazado en el dispositivo", "err")
        return ok

    def refresh_buttons(self):
        state = "disabled" if self.busy else "normal"
        self.btn_detect.configure(state=state)
        act = "disabled" if self.busy or not self.device else "normal"
        self.btn_fetch.configure(state=act)
        self.btn_update.configure(
            state=(
                "normal"
                if not self.busy and self.device and self.firmware_needed()
                else "disabled"
            )
        )
        dev = self.device
        if dev and not self.busy and dev["mode"] == "normal":
            initialized = dev.get("initialized")
            seed_btns = (self.btn_pass, self.btn_checkbak, self.btn_showseed, self.btn_wipe)
            self.btn_name.configure(state="normal")
            self.btn_genuine.configure(state="normal")
            if initialized is False:
                for b in seed_btns:
                    b.configure(state="disabled")
                self.btn_bip85.configure(state="disabled")
                self.btn_pwd.configure(state="disabled")
                self.btn_create.configure(state="normal")
                self.btn_restore.configure(state="normal")
            else:
                self.btn_pass.configure(state="normal")
                self.btn_checkbak.configure(state="normal")
                self.btn_showseed.configure(state="normal")
                self.btn_wipe.configure(state="normal")
                self.btn_pwd.configure(state="normal")
                self.btn_create.configure(state="disabled")
                self.btn_restore.configure(state="disabled")
                try:
                    ver = semver.VersionInfo.parse(dev.get("version") or "")
                except ValueError:
                    ver = None
                self.btn_bip85.configure(
                    state=(
                        "normal"
                        if ver and ver >= semver.VersionInfo(9, 18, 0)
                        else "disabled"
                    )
                )
        else:
            for b in (
                self.btn_pass, self.btn_checkbak, self.btn_showseed, self.btn_wipe,
                self.btn_create, self.btn_restore, self.btn_name, self.btn_bip85,
                self.btn_pwd, self.btn_genuine,
            ):
                b.configure(state="disabled")

    def firmware_needed(self):
        if not self.device or not self.release_v:
            return True
        if self.device["mode"] != "normal" or not self.device.get("version"):
            return True
        try:
            cur = semver.VersionInfo.parse(self.device["version"])
        except ValueError:
            return True
        return self.release_v > cur

    def device_ref(self):
        if not self.device:
            raise RuntimeError("Detecta el dispositivo primero")
        return self.device

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
            if tag is None and ("SHA-256" in msg or "firmado" in msg):
                tag = "accent"
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n", tag or ())
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        elif kind == "progress":
            cur, total = ev[1], ev[2]
            self.bar.configure(maximum=max(total, 1), value=cur)
            self.status.set(f"Flasheando… {cur * 100 // max(total, 1)}%")
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
                mode = "modo BOOTLOADER" if self.device["mode"] == "bootloader" else "modo normal"
                self.dev_var.set(
                    f"{EDITION_LABEL[(self.device['platform'], self.device['edition'])]} · "
                    f"{mode}"
                    + (f" · firmware v{self.device['version']}" if self.device["version"] else "")
                )
                if self.device.get("initialized") is not None:
                    self.mgmt_var.set(
                        f"«{self.device.get('name') or 'BitBox'}» · "
                        f"Wallet: {'inicializada' if self.device['initialized'] else 'sin crear'} · "
                        f"Passphrase: {'activada' if self.device['passphrase'] else 'desactivada'}"
                    )
                else:
                    self.mgmt_var.set(
                        "Emparejando para leer el estado del dispositivo…"
                    )
            else:
                self.dev_var.set("Sin detectar — conecta tu BitBox")
                self.mgmt_var.set(
                    "Detecta el dispositivo para gestionar passphrase, backup y borrado"
                )
        elif kind == "release":
            self.release_v, self.release_name = ev[1]
            if self.device and not self.firmware_needed():
                self.rel_var.set(
                    f"Actualizado — v{self.release_v} es la última disponible"
                )
            else:
                self.rel_var.set(
                    f"Disponible: v{self.release_v} ({self.release_name})"
                )
            self.refresh_buttons()
        elif kind == "confirm":
            ok = messagebox.askyesno("Confirmar", ev[1])
            self.confirm_result.append(ok)
            self.confirm_evt.set()
        elif kind == "pairing_show":
            code = ev[1]
            dlg = tk.Toplevel(self.root)
            dlg.title("Emparejamiento")
            dlg.configure(bg="#1c1c1c")
            dlg.transient(self.root)
            dlg.resizable(False, False)
            frm = ttk.Frame(dlg, padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(
                frm, text="Compara este código con la pantalla del BitBox",
                style="Subtitle.TLabel", justify="left",
            ).pack(anchor="w")
            ttk.Label(
                frm, text=code, font=("TkDefaultFont", 20, "bold"),
                style="Accent.TLabel",
            ).pack(anchor="w", pady=10)
            ttk.Label(
                frm, text=(
                    "Ambos deben ser IDÉNTICOS.\n"
                    "Si coinciden: confirma tocando ✓ en el dispositivo.\n"
                    "Si NO coinciden: toca ✗ y desconecta el dispositivo\n"
                    "(podría estar manipulado)."
                ),
                style="Subtitle.TLabel", justify="left",
            ).pack(anchor="w")
            self.pairing_dlg = dlg
        elif kind == "pairing_close":
            dlg = getattr(self, "pairing_dlg", None)
            if dlg is not None:
                dlg.destroy()
                self.pairing_dlg = None
        elif kind == "choice":
            _, title, msg, options = ev
            dlg = tk.Toplevel(self.root)
            dlg.title(title)
            dlg.configure(bg="#1c1c1c")
            dlg.transient(self.root)
            dlg.resizable(False, False)
            frm = ttk.Frame(dlg, padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text=msg, justify="left").pack(anchor="w", pady=(0, 12))
            btns = ttk.Frame(frm)
            btns.pack(fill="x")

            def pick(text):
                self.choice_result.append(text)
                self.choice_evt.set()
                dlg.destroy()

            for text in options:
                ttk.Button(btns, text=text, command=lambda t=text: pick(t)).pack(
                    side="left", padx=4
                )
            ttk.Button(btns, text="Cancelar", command=lambda: pick(None)).pack(
                side="right", padx=4
            )
            dlg.protocol("WM_DELETE_WINDOW", lambda: pick(None))
            dlg.grab_set()
            self.root.wait_window(dlg)
        elif kind == "ask_text":
            _, title, msg, initial = ev
            dlg = tk.Toplevel(self.root)
            dlg.title(title)
            dlg.configure(bg="#1c1c1c")
            dlg.transient(self.root)
            dlg.resizable(False, False)
            frm = ttk.Frame(dlg, padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text=msg, justify="left").pack(anchor="w", pady=(0, 10))
            var = tk.StringVar(value=initial)
            ent = ttk.Entry(frm, textvariable=var, width=36)
            ent.pack(fill="x")
            ent.focus_set()

            def done(res):
                self.text_result.append(res)
                self.text_evt.set()
                dlg.destroy()

            btns = ttk.Frame(frm)
            btns.pack(fill="x", pady=(14, 0))
            ttk.Button(btns, text="Cancelar", command=lambda: done(None)).pack(
                side="right"
            )
            ttk.Button(
                btns, text="Aceptar", style="Primary.TButton",
                command=lambda: done(var.get()),
            ).pack(side="right", padx=6)
            dlg.protocol("WM_DELETE_WINDOW", lambda: done(None))
            ent.bind("<Return>", lambda e: done(var.get()))
            dlg.grab_set()
            self.root.wait_window(dlg)
        elif kind == "show_text":
            title, text = ev[1], ev[2]
            dlg = tk.Toplevel(self.root)
            dlg.title(title)
            dlg.configure(bg="#1c1c1c")
            dlg.transient(self.root)
            frm = ttk.Frame(dlg, padding=18)
            frm.pack(fill="both", expand=True)
            txt = tk.Text(
                frm, height=6, width=64, font=self.log_font, wrap="none",
                background="#0f0f0f", foreground="#b5b5b5", relief="flat",
            )
            txt.insert("1.0", text)
            txt.configure(state="disabled")
            txt.pack(fill="both", expand=True)
            ttk.Button(frm, text="Cerrar", command=dlg.destroy).pack(pady=(12, 0))
            dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
            dlg.grab_set()
        elif kind == "ask_save":
            data, suggested = ev[1], ev[2]
            path = filedialog.asksaveasfilename(
                initialfile=suggested, defaultextension=".bin",
                filetypes=[("Firmware", "*.bin")],
            )
            if path:
                with open(path, "wb") as fh:
                    fh.write(data)
                self.log(f"Guardado en {path}", "ok")
            else:
                self.log("No se guardó nada en disco")
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

    # ---------- jobs ----------

    def start_detect(self):
        self.set_busy(True)
        self.rel_var.set("")
        self.release_v = None
        self.log("Buscando dispositivo…")
        self.worker(self.detect_job)

    def detect_job(self):
        normals, bootloaders = usb_scan()
        if normals:
            info = normals[0]
            platform, edition = NORMAL_IDS[info["product_string"]]
            version = str(bbdevices.parse_device_version(info["serial_number"]))
            self.q.put((
                "device",
                {"mode": "normal", "platform": platform, "edition": edition,
                 "version": version, "info": info, "initialized": None, "passphrase": None},
            ))
            self.log(f"Detectado {EDITION_LABEL[(platform, edition)]} · firmware v{version}")
        elif bootloaders:
            info = bootloaders[0]
            platform, edition = BOOTLOADER_IDS[info["product_string"]]
            self.q.put((
                "device",
                {"mode": "bootloader", "platform": platform, "edition": edition,
                 "version": None, "info": info, "initialized": None, "passphrase": None},
            ))
            self.log(
                f"Detectado {EDITION_LABEL[(platform, edition)]} en modo BOOTLOADER",
            )
        else:
            self.q.put(("device", None))
            self.log("No se encontró ningún BitBox (ni firmware ni bootloader)")
            return
        release = fetch_json(GITHUB_API + "/releases/latest")
        asset = pick_asset(release, platform, edition)
        if asset:
            v = ASSET_RE.search(asset["name"]).group(3)
            self.q.put((
                "release",
                (semver.VersionInfo.parse(v), release["name"]),
            ))
        if normals:
            self.log("Leyendo estado del dispositivo (emparejamiento la primera vez)…")
            self.refresh_device_state()

    def start_fetch_only(self):
        self.set_busy(True)
        self.log("Descargando y verificando firmware…")
        self.worker(self.fetch_only_job)

    def fetch_only_job(self):
        ref = self.device_ref()
        release = fetch_json(GITHUB_API + "/releases/latest")
        asset = pick_asset(release, ref["platform"], ref["edition"])
        if not asset:
            raise RuntimeError("No hay asset para esta edición en el último release")
        data = download(asset["browser_download_url"], lambda m: self.log(m))
        verify_blob(data, asset, ref["platform"], ref["edition"], lambda m: self.log(m))
        self.q.put(("ask_save", data, asset["name"]))
        self.q.put(("success", "Descarga y verificación completadas"))

    def start_update(self):
        self.set_busy(True)
        self.rel_var.set("")
        self.log("Iniciando actualización…")
        self.worker(self.update_job)

    def update_job(self):
        normals, bootloaders = usb_scan()
        platform = edition = info = None
        current = None
        mode = None
        if normals:
            info = normals[0]
            platform, edition = NORMAL_IDS[info["product_string"]]
            current = bbdevices.parse_device_version(info["serial_number"])
            mode = "normal"
            self.q.put((
                "device", {"mode": "normal", "platform": platform, "edition": edition,
                           "version": str(current), "info": info,
                           "initialized": None, "passphrase": None},
            ))
        elif bootloaders:
            info = bootloaders[0]
            platform, edition = BOOTLOADER_IDS[info["product_string"]]
            mode = "bootloader"
            self.q.put((
                "device", {"mode": "bootloader", "platform": platform, "edition": edition,
                           "version": None, "info": info,
                           "initialized": None, "passphrase": None},
            ))
        else:
            raise RuntimeError("No se detectó ningún BitBox (ni firmware ni bootloader)")

        latest = fetch_json(GITHUB_API + "/releases/latest")
        asset = pick_asset(latest, platform, edition)
        if not asset:
            raise RuntimeError("No hay asset para esta edición en el último release")
        target_v = ASSET_RE.search(asset["name"]).group(3)
        self.q.put((
            "release",
            (semver.VersionInfo.parse(target_v), latest["name"]),
        ))
        if mode == "normal" and current is not None and semver.VersionInfo.parse(target_v) <= current:
            self.log("El dispositivo ya tiene la última versión — nada que instalar")
            return

        steps = []
        if current is not None:
            steps = upgrade_path(current, latest.get("body") or "")
        images = []
        for step_v in steps:
            rel = fetch_json(RELEASE_BY_TAG.format(step_v))
            a = pick_asset(rel, platform, edition)
            if not a:
                raise RuntimeError(f"No hay asset v{step_v} para esta edición")
            images.append((step_v, a, rel))
        images.append((target_v, asset, latest))

        hashes = parse_device_hashes(latest.get("body") or "")
        dev_hash = hashes.get(EDITION_LABEL[(platform, edition)])

        chain = " → ".join(v for v, _, _ in images)
        msg = (
            f"Se instalará la ruta: {chain}\n"
            f"Edición: {EDITION_LABEL[(platform, edition)]}\n"
            f"Origen: github.com/BitBoxSwiss/bitbox02-firmware (contenedor firmado; "
            f"SHA-256 cuando está publicado; la firma la valida el bootloader)\n"
            + (f"Hash que mostrará el dispositivo: {dev_hash}\n" if dev_hash else "")
            + "\nNo desconectes el dispositivo durante el flasheo. ¿Continuar?"
        )
        if not self.ask_confirm(msg):
            self.log("Cancelado por el usuario")
            return

        blobs = []
        for i, (v, a, _) in enumerate(images):
            data = download(a["browser_download_url"], lambda m: self.log(m))
            verify_blob(
                data, a, platform, edition, lambda m: self.log(m),
                require_digest=i == len(images) - 1,
            )
            blobs.append((v, data))

        for i, (v, data) in enumerate(blobs):
            final = i == len(blobs) - 1
            if mode == "normal":
                self.log(
                    f"Reiniciando a bootloader (v{v})… confirma en la pantalla del dispositivo"
                )
                client = make_client(info, self.noise, lambda m: self.log(m))
                try:
                    client.reboot()
                finally:
                    client.close()
                wait_for(
                    lambda: bool(bbdevices.get_any_bitbox02_bootloaders()),
                    90, lambda m: self.log(m), "dispositivo en modo bootloader",
                )
                mode = "bootloader"
            bl_info = bbdevices.get_any_bitbox02_bootloader()
            bl = Bootloader(hid_transport(bl_info), bl_info)
            try:
                self.log(f"Flasheando v{v}…")
                bl.flash_signed_firmware(
                    data, progress_callback=lambda p: self.progress(int(p * 100), 100)
                )
                if final and dev_hash:
                    bl.get_hashes(
                        display_firmware_hash=True, display_signing_keydata_hash=True
                    )
                    self.log("Hash mostrado en pantalla — compara con el del registro")
                self.log("Reiniciando…")
                bl.reboot()
            finally:
                try:
                    bl.close()
                except Exception:
                    pass
            if not final:
                wait_for(
                    lambda: bool(bbdevices.get_any_bitbox02s()),
                    90, lambda m: self.log(m), "dispositivo arranque con firmware intermedio",
                )
                normals = bbdevices.get_any_bitbox02s()
                info = normals[0]
                mode = "normal"

        self.q.put((
            "success",
            f"Actualización completada: v{target_v}. El dispositivo se reinició solo.",
        ))
        self.detect_job()

    # ---------- gestión ----------

    def mgmt_with_client(self, fn):
        normals, _ = usb_scan()
        if not normals:
            raise RuntimeError("No hay BitBox en modo normal conectado")
        info = normals[0]
        client = make_client(info, self.noise, lambda m: self.log(m))
        try:
            try:
                fn(client, info)
            except Bitbox02Exception as e:
                if "wrong state" not in str(e).lower():
                    raise
                self.log(
                    "Dispositivo bloqueado — reconectando para pedir la contraseña…"
                )
                client.close()
                normals2, _ = usb_scan()
                if not normals2:
                    raise RuntimeError("El dispositivo desapareció") from e
                info = normals2[0]
                client = make_client(
                    info, self.noise,
                    lambda m: self.log(m), unlock_first=True,
                )
                fn(client, info)
        finally:
            client.close()

    def refresh_device_state(self):
        def fn(client, info):
            di = client.device_info()
            platform, edition = NORMAL_IDS[info["product_string"]]
            self.q.put((
                "device",
                {"mode": "normal", "platform": platform, "edition": edition,
                 "version": di["version"], "info": info,
                 "initialized": di["initialized"],
                 "passphrase": di["mnemonic_passphrase_enabled"],
                 "name": di.get("name")},
            ))

        try:
            self.mgmt_with_client(fn)
        except Exception:
            pass

    def start_toggle_passphrase(self):
        self.set_busy(True)
        self.worker(self.mgmt_passphrase_job)

    def mgmt_passphrase_job(self):
        def fn(client, info):
            di = client.device_info()
            if di["mnemonic_passphrase_enabled"]:
                client.disable_mnemonic_passphrase()
                self.q.put(("success", "Passphrase desactivada"))
            else:
                client.enable_mnemonic_passphrase()
                self.q.put(("success", "Passphrase activada"))
        self.mgmt_with_client(fn)
        self.refresh_device_state()

    def start_check_backup(self):
        self.set_busy(True)
        self.log("Verificando backup de la microSD…")
        self.worker(self.mgmt_check_backup_job)

    def mgmt_check_backup_job(self):
        def fn(client, info):
            result = client.check_backup(silent=False)
            if result is None:
                self.log("No hay backup en la microSD (o no coincide)")
            else:
                self.q.put(("success", f"Backup verificado: {result}"))
        self.mgmt_with_client(fn)

    def start_show_mnemonic(self):
        self.set_busy(True)
        self.worker(self.mgmt_show_mnemonic_job)

    def mgmt_show_mnemonic_job(self):
        if not self.ask_confirm(
            "MOSTRAR LA SEED en pantalla del dispositivo.\n\n"
            "Asegúrate de que nadie te observa ni hay cámaras. ¿Continuar?"
        ):
            self.log("Cancelado por el usuario")
            return

        def fn(client, info):
            client.show_mnemonic()
            self.q.put(("success", "Seed mostrada en el dispositivo"))
        self.mgmt_with_client(fn)

    def start_wipe(self):
        self.set_busy(True)
        self.worker(self.mgmt_wipe_job)

    def mgmt_wipe_job(self):
        if not self.ask_confirm(
            "BORRAR EL DISPOSITIVO (factory reset).\n\n"
            "La seed se elimina y NO es recuperable sin tu backup físico.\n"
            "El nombre del dispositivo y los ajustes también se pierden.\n\n"
            "¿Borrar?"
        ):
            self.log("Cancelado por el usuario")
            return

        def fn(client, info):
            if client.reset():
                self.q.put(("success", "Dispositivo borrado — como nuevo"))
            else:
                raise RuntimeError("El borrado fue cancelado o falló")
        self.mgmt_with_client(fn)
        self.refresh_device_state()

    def start_create_wallet(self):
        self.set_busy(True)
        self.worker(self.mgmt_create_job)

    def mgmt_create_job(self):
        if not self.ask_confirm(
            "CREAR WALLET NUEVA (seed nueva).\n\n"
            "El dispositivo generará una seed y te pedirá definir una\n"
            "contraseña con el botón táctil. Harás el backup a continuación.\n\n"
            "¿Continuar?"
        ):
            self.log("Cancelado por el usuario")
            return
        entropy = self.ask_choice(
            "Crear wallet",
            "Elige el tamaño de la seed:",
            ["24 palabras", "12 palabras"],
        )
        if entropy is None:
            self.log("Cancelado por el usuario")
            return
        size = 16 if entropy.startswith("12") else 32

        def fn(client, info):
            self.log("Sigue las instrucciones en la pantalla del dispositivo…")
            while not client.set_password(entropy_size=size):
                self.log("Las contraseñas no coincidieron — reinténtalo en el dispositivo")
            self.log("Seed creada y contraseña definida")
        self.mgmt_with_client(fn)

        backup = self.ask_choice(
            "Backup",
            "Haz y verifica el backup ANTES de enviar fondos. ¿Cómo?",
            ["Backup a microSD", "Copiar palabras en pantalla", "Ahora no"],
        )
        if backup is None or backup == "Ahora no":
            self.log(
                "SIN BACKUP: la wallet solo vive en el dispositivo — "
                "hazlo cuanto antes desde «Verificar backup»/«Mostrar seed»",
                "err",
            )
        else:
            def fn2(client, info):
                if backup.startswith("Backup"):
                    if not client.create_backup():
                        raise RuntimeError("La creación del backup falló")
                    self.q.put(("success", "Backup creado en la microSD"))
                else:
                    client.show_mnemonic()
                    self.q.put(("success", "Palabras mostradas — cópialas y verifica"))
            self.mgmt_with_client(fn2)
        self.refresh_device_state()

    def start_restore_backup(self):
        self.set_busy(True)
        self.log("Leyendo backups de la microSD…")
        self.worker(self.mgmt_restore_job)

    def mgmt_restore_job(self):
        backups = []

        def fn(client, info):
            backups.extend(client.list_backups())

        self.mgmt_with_client(fn)
        if not backups:
            self.log("No hay backups en la microSD")
            return
        labels = [
            f"{name or 'backup'} · {date:%Y-%m-%d %H:%M}" for _, name, date in backups
        ]
        chosen = self.ask_choice(
            "Restaurar backup", "Elige el backup a restaurar:", labels
        )
        if chosen is None:
            self.log("Cancelado por el usuario")
            return
        backup_id = backups[labels.index(chosen)][0]
        self.log(f"Restaurando {chosen} — confirma en el dispositivo…")

        def fn2(client, info):
            try:
                client.restore_backup(backup_id)
            except OSError:
                pass  # el dispositivo se reinicia al restaurar
            self.q.put(("success", "Backup restaurado — retira la microSD"))

        self.mgmt_with_client(fn2)
        self.refresh_device_state()


    def start_rename(self):
        self.set_busy(True)
        self.worker(self.mgmt_rename_job)

    def mgmt_rename_job(self):
        current = (self.device or {}).get("name") or ""
        name = self.ask_text(
            "Renombrar dispositivo",
            "Nombre nuevo (1-64 caracteres).\n"
            "El firmware exige un nombre: no se puede dejar vacío.",
            initial=current,
        )
        if name is None:
            self.log("Cancelado por el usuario")
            return
        name = name.strip()
        if not name or len(name) > 64:
            raise RuntimeError("El nombre debe tener entre 1 y 64 caracteres")

        def fn(client, info):
            client.set_device_name(name)
            self.q.put(("success", f"Dispositivo renombrado a «{name}»"))
        self.mgmt_with_client(fn)
        self.refresh_device_state()

    def start_bip85(self):
        self.set_busy(True)
        self.worker(self.mgmt_bip85_job)

    def mgmt_bip85_job(self):
        app = self.ask_choice(
            "BIP85",
            "Entropía determinista derivada de tu seed (BIP-85).\n"
            "Elige la aplicación:",
            [
                "BIP39 — mnemonic en pantalla del dispositivo",
                "Lightning — seed para wallet caliente",
            ],
        )
        if app is None:
            self.log("Cancelado por el usuario")
            return
        if app.startswith("BIP39"):
            def fn(client, info):
                client.bip85_bip39()
                self.q.put((
                    "success",
                    "Mnemonic BIP85 mostrado en el dispositivo — cópialo en privado",
                ))
            self.mgmt_with_client(fn)
            return
        if not self.ask_confirm(
            "BIP85 LIGHTNING.\n\n"
            "Se generará la seed (cuenta 0) para una wallet Lightning CALIENTE.\n"
            "A diferencia del resto de esta app, el resultado SÍ pasa por\n"
            "este ordenador: es necesario para importarla en tu nodo.\n"
            "Hazlo solo si vas a usarla ahora.\n\n"
            "¿Continuar?"
        ):
            self.log("Cancelado por el usuario")
            return

        def fn(client, info):
            seed = client.bip85_ln()
            self.q.put((
                "show_text",
                "BIP85 Lightning (cuenta 0)",
                seed.hex()
                + "\n\nSeed en hex — impórtala en tu nodo Lightning y trátala\n"
                "como lo que es: clave privada de fondos.",
            ))
            self.q.put(("success", "Seed Lightning BIP85 generada"))
        self.mgmt_with_client(fn)


    def start_genuine(self):
        self.set_busy(True)
        self.log("Verificando autenticidad del dispositivo…")
        self.worker(self.mgmt_genuine_job)

    def mgmt_genuine_job(self):
        self.noise._last_attestation = None

        def fn(client, info):
            pass
        self.mgmt_with_client(fn)
        result = self.noise._last_attestation
        if result is True:
            self.q.put((
                "success",
                "DISPOSITIVO GENUINO — atestación criptográfica firmada por "
                "las claves raíz de Shift verificada",
            ))
        else:
            self.q.put((
                "error",
                "ATESTACIÓN FALLIDA — el dispositivo no ha podido demostrar su "
                "autenticidad. NO lo uses con fondos reales.",
            ))

    def start_change_password(self):
        self.set_busy(True)
        self.worker(self.mgmt_password_job)

    def mgmt_password_job(self):
        if not self.ask_confirm(
            "CAMBIAR LA CONTRASEÑA DEL DISPOSITIVO.\n\n"
            "La seed NO cambia: solo se re-encripta. Introducirás la\n"
            "contraseña nueva dos veces en el dispositivo, alternando\n"
            "caracteres con el toque (como siempre).\n\n"
            "¿Continuar?"
        ):
            self.log("Cancelado por el usuario")
            return

        def fn(client, info):
            if not client.set_password():
                raise RuntimeError("Las contraseñas no coincidieron — reinténtalo")
            self.q.put(("success", "Contraseña cambiada — la seed sigue intacta"))
        self.mgmt_with_client(fn)


def selftest():
    for (platform, edition), label in EDITION_LABEL.items():
        release = fetch_json(GITHUB_API + "/releases/latest")
        asset = pick_asset(release, platform, edition)
        if not asset:
            raise RuntimeError(f"No hay asset para {label}")
        data = download(asset["browser_download_url"], print)
        verify_blob(data, asset, platform, edition, print)
        v = ASSET_RE.search(asset["name"]).group(3)
        print(f"SELFTEST OK · {label} v{v} · {len(data)} bytes")
    return 0


def main():
    parser = argparse.ArgumentParser(description="BitBox Firmware Updater sin BitBoxApp")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--libversion", action="store_true")
    args = parser.parse_args()

    if args.libversion:
        print(f"bitbox-updater + bitbox02 lib {pkg_version('bitbox02')}")
        return 0
    if args.selftest:
        return selftest()
    if not HAVE_TK:
        print("Falta tkinter (python3-tk en Debian/Ubuntu).")
        return 1

    root = tk.Tk()
    try:
        _icon = tk.PhotoImage(
            file=os.path.expanduser("~/.local/share/icons/hicolor/256x256/apps/bitbox-updater.png")
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
