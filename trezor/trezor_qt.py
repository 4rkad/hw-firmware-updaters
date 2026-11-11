#!/usr/bin/env python3
"""Trezor Firmware Updater — interfaz PySide6 (fase A).

Dispositivo, firmware, actualización, descarga verificada y registro.
La gestión de wallet (PIN, passphrase, backup, recover) queda para la fase B.
"""

from __future__ import annotations

import html
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))

from ui_common import (
    ChoiceDialog,
    ConfirmDialog,
    DIM,
    GREEN,
    InfoDialog,
    InputDialog,
    LOG_COLORS,
    ORANGE,
    QSS,
    RED,
    Job,
    TextDialog,
    card,
    install_font,
    kv,
    round_pixmap,
)

ROOT = Path(__file__).resolve().parent
def _res(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


ICON = _res("icons", "trezor.png")
BACKEND_PATH = _res("trezor-updater.py") if getattr(sys, "_MEIPASS", None) else ROOT / "trezor-updater.py"

def _log_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "trezor-updater.log"



spec = importlib.util.spec_from_file_location("trezor_backend", BACKEND_PATH)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

LANG_NAMES = {
    "cs-CZ": "Čeština",
    "de-DE": "Deutsch",
    "en-US": "English",
    "en-GB": "English",
    "es-ES": "Español",
    "fr-FR": "Français",
    "it-IT": "Italiano",
    "pt-BR": "Português (Brasil)",
    "pt-PT": "Português",
    "ru-RU": "Русский",
    "tr-TR": "Türkçe",
    "ja-JP": "日本語",
    "zh-CN": "中文",
    "pl-PL": "Polski",
    "nl-NL": "Nederlands",
    "sv-SE": "Svenska",
    "da-DK": "Dansk",
    "nb-NO": "Norsk",
    "fi-FI": "Suomi",
    "el-GR": "Ελληνικά",
    "hu-HU": "Magyar",
    "ro-RO": "Română",
    "uk-UA": "Українська",
}


BACKUP_LABELS = {0: "No disponible", 1: "Pendiente", 2: "Hecho"}


def backup_label(value):
    try:
        return BACKUP_LABELS.get(int(value), f"Desconocido ({value})")
    except Exception:
        return "—"


def lang_name(code):
    if not code:
        return "English"
    if code in LANG_NAMES:
        return LANG_NAMES[code]
    base = code.split("-")[0].lower()
    for known, name in LANG_NAMES.items():
        if known.split("-")[0].lower() == base:
            return name
    return code


class TrezorWindow(QMainWindow):
    pin_requested = Signal(str)
    word_requested = Signal()
    code_requested = Signal()
    log_requested = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Actualizador de firmware · Trezor (no oficial)")
        self.setMinimumSize(980, 620)
        self.resize(1180, 820)
        self.setStyleSheet(QSS)
        self.setWindowIcon(self._icon())
        self.jobs: set[Job] = set()
        self.active_job = None
        self.busy = False
        self.info = None
        self.version = None
        self.release = None
        self.has_device = False
        self.edition_btc = None
        self.pin_evt = threading.Event()
        self.pin_res = []
        self.word_evt = threading.Event()
        self.word_res = []
        self.code_evt = threading.Event()
        self.code_res = []
        self.pin_requested.connect(self.show_pin_dialog)
        self.word_requested.connect(self.show_word_dialog)
        self.code_requested.connect(self.show_code_dialog)
        self.log_requested.connect(self.append_log)

        # El Safe 3 usa THP: el dispositivo muestra un código de 6 dígitos.
        import trezorlib.cli as tzcli
        tzcli.get_code_entry_code = self.code_callback

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(14)
        outer.addLayout(self.header())
        outer.addWidget(self.content(), 1)
        self.clear_device()
        self.append_log("Buscando dispositivo Trezor…", "dim")
        QTimer.singleShot(300, self.detect)
        self.watch = QTimer(self)
        self.watch.timeout.connect(self.watch_device)
        self.watch.start(3000)

    def _icon(self):
        from PySide6.QtGui import QIcon

        return QIcon(str(ICON))

    # ------------------------------------------------------------------ UI
    def header(self):
        row = QHBoxLayout()
        row.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(round_pixmap(ICON, 54))
        logo.setFixedSize(54, 54)
        row.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("Actualizador de firmware · Trezor")
        title.setObjectName("title")
        sub = QLabel("Firmware y verificación sin Trezor Suite · proyecto no oficial")
        sub.setObjectName("dim")
        titles.addWidget(title)
        titles.addWidget(sub)
        row.addLayout(titles)
        row.addStretch(1)
        self.status_chip = QLabel("● Sin dispositivo")
        self.status_chip.setObjectName("chipMuted")
        row.addWidget(self.status_chip)
        self.device_chip = QLabel("")
        self.device_chip.setObjectName("dim")
        row.addWidget(self.device_chip)
        return row

    def content(self):
        device = self.device_card(); device.setMinimumHeight(280)
        security = self.security_card(); security.setMinimumHeight(300)
        firmware = self.firmware_card(); firmware.setMinimumHeight(280)
        release = self.release_card(); release.setMinimumHeight(300)
        activity = self.activity_card(); activity.setMinimumHeight(280)
        manage = self.manage_card(); manage.setMinimumHeight(300)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.addWidget(device, 0, 0)
        grid.addWidget(security, 1, 0)
        grid.addWidget(firmware, 0, 1)
        grid.addWidget(release, 1, 1)
        grid.addWidget(activity, 0, 2)
        grid.addWidget(manage, 1, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 4)
        grid.setColumnStretch(2, 3)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        container = QWidget()
        container.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    def device_card(self):
        frame, lay = card("DISPOSITIVO")
        row = QHBoxLayout()
        self.dev_label = QLabel("Conecta tu Trezor")
        self.dev_label.setObjectName("big")
        row.addWidget(self.dev_label, 1)
        self.btn_refresh = QPushButton("Buscar")
        self.btn_refresh.setObjectName("secondary")
        self.btn_refresh.clicked.connect(self.detect)
        row.addWidget(self.btn_refresh)
        lay.addLayout(row)
        self.model_row = kv("Modelo", "—")
        self.fw_row = kv("Firmware", "—")
        self.mode_row = kv("Modo", "—")
        self.vendor_row = kv("Vendor", "—")
        self.wallet_row = kv("Wallet", "—")
        for r in (self.model_row, self.fw_row, self.mode_row, self.vendor_row, self.wallet_row):
            lay.addLayout(r)
        lay.addStretch(1)
        return frame

    def security_card(self):
        frame, lay = card("SEGURIDAD")
        self.pin_row = kv("PIN", "—")
        self.pass_row = kv("Passphrase", "—")
        self.lang_row = kv("Idioma", "—")
        for r in (self.pin_row, self.pass_row, self.lang_row):
            lay.addLayout(r)
        self.backup_chip = QLabel("")
        self.backup_chip.setObjectName("chipMuted")
        self.backup_chip.hide()
        lay.addWidget(self.backup_chip, alignment=Qt.AlignLeft)
        lay.addStretch(1)
        return frame

    def firmware_card(self):
        frame, lay = card()
        top = QHBoxLayout()
        h = QLabel("FIRMWARE")
        h.setObjectName("h2")
        top.addWidget(h)
        top.addStretch(1)
        self.fw_chip = QLabel("Sin datos")
        self.fw_chip.setObjectName("chipMuted")
        top.addWidget(self.fw_chip)
        lay.addLayout(top)

        versions = QHBoxLayout()
        versions.setSpacing(12)
        self.current_label = QLabel("—")
        self.current_label.setStyleSheet("color: #8f8f8f; font-size: 22px; font-weight: 700;")
        arrow = QLabel("→")
        arrow.setStyleSheet("color: #6f6f6f; font-size: 22px;")
        self.target_label = QLabel("—")
        self.target_label.setStyleSheet(f"color: {ORANGE}; font-size: 26px; font-weight: 800;")
        versions.addWidget(self.current_label)
        versions.addWidget(arrow)
        versions.addWidget(self.target_label)
        versions.addStretch(1)
        lay.addLayout(versions)

        edition_row = QHBoxLayout()
        edition_label = QLabel("Edición")
        edition_label.setObjectName("dim")
        edition_row.addWidget(edition_label)
        self.edition_value = QLabel("—")
        self.edition_value.setStyleSheet("font-weight: 600;")
        edition_row.addWidget(self.edition_value)
        self.btn_edition = QPushButton("Cambiar edición…")
        self.btn_edition.setObjectName("secondary")
        self.btn_edition.clicked.connect(self.change_edition)
        edition_row.addWidget(self.btn_edition)
        edition_row.addStretch(1)
        lay.addLayout(edition_row)

        self.notes_label = QLabel("Conecta el dispositivo para consultar la versión.")
        self.notes_label.setObjectName("dim")
        self.notes_label.setWordWrap(True)
        lay.addWidget(self.notes_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        lay.addWidget(self.progress)
        self.progress_label = QLabel("Listo")
        self.progress_label.setObjectName("dim")
        lay.addWidget(self.progress_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_update = QPushButton("Actualizar firmware")
        self.btn_update.setObjectName("primary")
        self.btn_update.clicked.connect(self.start_update)
        self.btn_download = QPushButton("Solo descargar")
        self.btn_download.setObjectName("secondary")
        self.btn_download.clicked.connect(self.download_only)
        buttons.addWidget(self.btn_update)
        buttons.addWidget(self.btn_download)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addStretch(1)
        return frame

    def release_card(self):
        frame, lay = card("RELEASE")
        self.rel_version = kv("Versión", "—")
        self.rel_edition = kv("Edición", "—")
        self.rel_min = kv("Actualizable desde", "—")
        self.rel_fp = kv("Fingerprint", "—")
        for r in (self.rel_version, self.rel_edition, self.rel_min, self.rel_fp):
            lay.addLayout(r)
        self.rel_min.itemAt(2).widget().setToolTip(
            "Versión instalada mínima para saltar directamente a esta release. "
            "Si tu firmware es anterior, la app instala pasos intermedios."
        )
        lay.addStretch(1)
        return frame

    def activity_card(self):
        frame, lay = card("ACTIVIDAD")
        self.logbox = QTextEdit()
        self.logbox.setObjectName("log")
        self.logbox.setReadOnly(True)
        self.logbox.setMinimumHeight(140)
        lay.addWidget(self.logbox, 1)
        more = QPushButton("Ver registro completo")
        more.setObjectName("ghost")
        more.clicked.connect(self.show_log)
        lay.addWidget(more, alignment=Qt.AlignLeft)
        lay.addStretch(1)
        return frame

    def manage_card(self):
        frame, lay = card("GESTIÓN")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.manage_btns = []
        actions = (
            ("Cambiar PIN", self.change_pin),
            ("Quitar PIN", self.remove_pin),
            ("Passphrase", self.toggle_passphrase),
            ("Idioma", self.change_language),
            ("Crear wallet", self.create_wallet),
            ("Borrar wallet", self.wipe_wallet),
            ("Backup / verificar", self.backup_wallet),
            ("Recover", self.recover_wallet),
        )
        for i, (text, slot) in enumerate(actions):
            btn = QPushButton(text)
            btn.setObjectName("secondary")
            btn.clicked.connect(slot)
            grid.addWidget(btn, i // 2, i % 2)
            self.manage_btns.append(btn)
        lay.addLayout(grid)
        lay.addStretch(1)
        return frame

    # ------------------------------------------------------------------ jobs
    def submit(self, fn, on_result=None, on_error=None, busy_text="Trabajando…", timeout_ms=120000):
        if self.busy:
            return False
        job = Job(fn)
        self.active_job = job
        job.log.connect(self.append_log)
        job.progress.connect(self.set_progress)
        job.result.connect(lambda r, j=job: self.finish_job(j, r, on_result))
        job.error.connect(lambda e, j=job: self.fail_job(j, e, on_error))
        job.finished.connect(lambda j=job: self.jobs.discard(j))
        self.jobs.add(job)
        self.set_busy(True, busy_text)
        QTimer.singleShot(timeout_ms, lambda: self.check_timeout(job))
        job.start()
        return True

    def check_timeout(self, job):
        if self.active_job is job:
            self.active_job = None
            self.append_log("La operación ha tardado demasiado; se libera la interfaz.", "warn")
            self.set_busy(False)

    def finish_job(self, job, result, callback):
        if job is not self.active_job:
            return
        self.active_job = None
        self.jobs.discard(job)
        self.set_busy(False)
        if callback:
            callback(result)

    def fail_job(self, job, error, callback):
        if job is not self.active_job:
            return
        self.active_job = None
        self.jobs.discard(job)
        self.append_log(error, "warn" if "No Trezor device found" in error else "error")
        self.set_busy(False)
        self.status_chip.setText("● Error")
        self.status_chip.setObjectName("chipBad")
        self.restyle(self.status_chip)
        if callback:
            callback(error)
        else:
            InfoDialog(self, "Trezor Updater", error, error=True).exec()

    def set_busy(self, busy, text=None):
        self.busy = busy
        self.apply_buttons()
        if busy:
            self.status_chip.setText(f"● {text or 'Trabajando…'}")
            self.status_chip.setObjectName("chipWarn")
        else:
            self.update_status()
        self.restyle(self.status_chip)

    def apply_buttons(self):
        busy = self.busy
        self.btn_refresh.setEnabled(not busy)
        self.btn_update.setEnabled(not busy and self.has_device and self._can_update())
        self.btn_download.setEnabled(not busy and self.has_device)
        info = self.info or {}
        initialized = bool(info.get("initialized"))
        locked = bool(info.get("locked"))
        bootloader = bool(info.get("bootloader_mode"))
        base = not busy and self.has_device and not locked and not bootloader
        for b in getattr(self, "manage_btns", []):
            if b.text() in ("Crear wallet", "Recover", "Idioma"):
                b.setEnabled(base)
            else:
                b.setEnabled(base and initialized)
        self.btn_edition.setEnabled(not busy and self.has_device)

    def restyle(self, w):
        w.style().unpolish(w)
        w.style().polish(w)

    def update_status(self):
        if not self.info:
            text, name = "● Sin dispositivo", "chipMuted"
        else:
            text, name = "● Conectado", "chipOk"
        self.status_chip.setText(text)
        self.status_chip.setObjectName(name)
        self.restyle(self.status_chip)

    def append_log(self, msg, tag=""):
        color = LOG_COLORS.get(tag, LOG_COLORS[""])
        self.logbox.append(f'<span style="color:{color}">{html.escape(str(msg))}</span>')
        try:
            with open(_log_path(), "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{tag or 'info'}] {msg}\n")
        except Exception:
            pass
        self.logbox.verticalScrollBar().setValue(self.logbox.verticalScrollBar().maximum())

    def set_progress(self, cur, total):
        total = max(int(total or 0), 1)
        self.progress.setMaximum(total)
        self.progress.setValue(int(cur))
        self.progress_label.setText(f"{int(cur * 100 / total)} %")

    def show_log(self):
        TextDialog(self, "Registro", self.logbox.toPlainText()).exec()

    def set_row(self, row, value, color=None):
        if row is not None and row.count() >= 3:
            w = row.itemAt(2).widget()
            w.setText(value)
            w.setStyleSheet(f"color: {color}; font-weight: 600;" if color else "")

    def set_backup_chip(self, text, style):
        self.backup_chip.setText(text)
        self.backup_chip.setObjectName(style)
        self.restyle(self.backup_chip)
        self.backup_chip.show()

    def clear_device(self):
        self.has_device = False
        self.info = None
        self.edition_btc = None
        self.version = None
        self.release = None
        self.dev_label.setText("Sin dispositivo")
        self.device_chip.setText("")
        self.update_status()
        for row, value in (
            (self.model_row, "—"), (self.fw_row, "—"), (self.mode_row, "—"),
            (self.vendor_row, "—"), (self.wallet_row, "—"),
            (self.pin_row, "—"), (self.pass_row, "—"), (self.lang_row, "—"),
            (self.rel_version, "—"), (self.rel_edition, "—"), (self.rel_min, "—"), (self.rel_fp, "—"),
        ):
            self.set_row(row, value)
        self.current_label.setText("—")
        self.target_label.setText("—")
        self.notes_label.setText("Conecta el dispositivo para consultar la versión.")
        self.fw_chip.setText("Sin datos")
        self.fw_chip.setObjectName("chipMuted")
        self.restyle(self.fw_chip)
        if hasattr(self, "edition_value"):
            self.edition_value.setText("—")
        self.apply_buttons()


    # ------------------------------------------------------------------ gestión
    def pin_callback(self, request):
        code = getattr(request, "type", None)
        self.pin_evt.clear()
        self.pin_res.clear()
        self.pin_requested.emit(backend.PIN_DESCRIPTIONS.get(code, "Introduce el PIN"))
        self.pin_evt.wait()
        if not self.pin_res or self.pin_res[0] == "cancel":
            raise backend.Cancelled("Entrada de PIN cancelada")
        return self.pin_res[1]

    def show_pin_dialog(self, title):
        dlg = InputDialog(
            self,
            "PIN del Trezor",
            title + "\n\nMira la matriz del Trezor y escribe las POSICIONES (1-9):",
            password=True,
        )
        if not dlg.exec():
            self.pin_res[:] = ["cancel"]
        else:
            pin = dlg.value().strip()
            if pin and all(c in "123456789" for c in pin):
                self.pin_res[:] = ["ok", pin]
            else:
                InfoDialog(self, "PIN inválido", "Solo posiciones del 1 al 9.", error=True).exec()
                self.pin_res[:] = ["cancel"]
        self.pin_evt.set()

    def code_callback(self):
        self.append_log("El Trezor pide el código de seguridad de 6 dígitos…", "accent")
        self.code_evt.clear()
        self.code_res.clear()
        self.code_requested.emit()
        self.code_evt.wait()
        if not self.code_res or self.code_res[0] == "cancel":
            raise backend.Cancelled("Código de seguridad cancelado")
        return self.code_res[1]

    def show_code_dialog(self):
        dlg = InputDialog(
            self,
            "Código de seguridad",
            "Escribe el código de 6 dígitos que muestra el Trezor:",
        )
        ok = dlg.exec()
        code = "".join(c for c in dlg.value() if c.isdigit())
        if ok and len(code) == 6:
            self.append_log("Código de seguridad enviado al Trezor.", "dim")
            self.code_res[:] = ["ok", code]
        else:
            if ok:
                InfoDialog(self, "Código", "El código debe tener 6 dígitos.", error=True).exec()
            self.code_res[:] = ["cancel"]
        self.code_evt.set()

    def word_callback(self, request):
        rtype = getattr(request, "type", backend.messages.WordRequestType.Plain)
        if rtype != backend.messages.WordRequestType.Plain:
            raise backend.Cancelled("Entrada por matriz no soportada")
        self.word_evt.clear()
        self.word_res.clear()
        self.word_requested.emit()
        self.word_evt.wait()
        if not self.word_res or self.word_res[0] == "cancel":
            raise backend.Cancelled("Entrada de palabra cancelada")
        return self.word_res[1]

    def show_word_dialog(self):
        dlg = InputDialog(
            self,
            "Palabra del seed",
            "Escribe la palabra que pide la pantalla del Trezor:",
        )
        if dlg.exec() and dlg.value().strip():
            self.word_res[:] = ["ok", dlg.value().strip().lower()]
        else:
            self.word_res[:] = ["cancel"]
        self.word_evt.set()

    def button_callback(self, br):
        self.log_requested.emit("Confirma en la pantalla del dispositivo…", "dim")

    def managed_client(self, conn):
        client = conn.get_client()
        client.pin_callback = self.pin_callback
        client.button_callback = self.button_callback
        app = getattr(client, "app", None)
        if app is not None and app is not client:
            app.pin_callback = self.pin_callback
            app.button_callback = self.button_callback
        return client

    def _confirm(self, title, text):
        return ConfirmDialog(self, title, text, confirm="Sí", cancel="No").exec()

    def _run_manage(self, fn, busy_text, confirm=None, on_done=None):
        if self.busy or not self.has_device:
            return
        if confirm and not self._confirm("Trezor", confirm):
            return

        def job(log, progress):
            conn = backend.make_connection()
            try:
                client = self.managed_client(conn)
                fn(client, log)
            finally:
                conn.close()
            return True

        def done(_):
            if on_done:
                on_done()
            QTimer.singleShot(1200, self.detect)

        self.submit(
            job,
            on_result=done,
            busy_text=busy_text,
            timeout_ms=600000,
        )

    def toggle_passphrase(self):
        self._run_manage(
            lambda client, log: (
                backend.device.apply_settings(
                    backend.seedless_session(client),
                    use_passphrase=not client.features.passphrase_protection,
                ),
                log("Passphrase cambiada. Confirma en el dispositivo.", "ok"),
            ),
            "Cambiando passphrase…",
            "Se cambiará el ajuste de passphrase. Confirma en el dispositivo. ¿Continuar?",
        )

    def change_pin(self):
        self._run_manage(
            lambda client, log: (
                backend.device.change_pin(backend.seedless_session(client)),
                log("PIN configurado.", "ok"),
            ),
            "Configurando PIN…",
            "Se cambiará el PIN. Introduce el PIN actual y el nuevo en la matriz del Trezor. ¿Continuar?",
        )

    def remove_pin(self):
        self._run_manage(
            lambda client, log: (
                backend.device.change_pin(backend.seedless_session(client), remove=True),
                log("PIN eliminado.", "ok"),
            ),
            "Quitando PIN…",
            "Se quitará el PIN del dispositivo. Confirma con el PIN actual. ¿Continuar?",
        )

    def wipe_wallet(self):
        if not self._confirm(
            "Borrar wallet",
            "Se borrará la wallet del dispositivo.\n\n"
            "Si no tienes la seed respaldada, perderás el acceso a los fondos.\n"
            "¿Continuar?",
        ):
            return
        self._run_manage(
            lambda client, log: (
                backend.device.wipe(backend.seedless_session(client)),
                log("Wallet borrada.", "ok"),
            ),
            "Borrando wallet…",
        )

    def create_wallet(self):
        if not self._confirm(
            "Crear wallet",
            "Se creará una wallet nueva. Si ya hay una, se borrará.\n"
            "El Trezor mostrará la seed en pantalla; haz el backup.\n\n¿Continuar?",
        ):
            return
        options = [
            "12 palabras · BIP39",
            "24 palabras · BIP39",
            "20 palabras · SLIP39 (1 de 1)",
            "33 palabras · SLIP39 (1 de 1)",
            "Multishare · SLIP39 N de M",
            "Multishare con grupos · SLIP39 avanzado",
        ]
        picker = ChoiceDialog(self, "Backup", "Tipo de backup:", options, 0)
        if not picker.exec():
            return
        choice = options[picker.selected()]
        mapping = {
            options[0]: ("bip39", 128, backend.messages.BackupType.Bip39),
            options[1]: ("bip39", 256, backend.messages.BackupType.Bip39),
            options[2]: ("slip39", 128, backend.messages.BackupType.Slip39_Single_Extendable),
            options[3]: ("slip39", 256, backend.messages.BackupType.Slip39_Single_Extendable),
            options[4]: ("slip39", 128, backend.messages.BackupType.Slip39_Basic_Extendable),
            options[5]: ("slip39", 128, backend.messages.BackupType.Slip39_Advanced_Extendable),
        }
        _, strength, backup_type = mapping[choice]
        dlg = InputDialog(self, "Nombre", "Nombre del dispositivo:", "Trezor")
        if not dlg.exec():
            return
        label = dlg.value()
        pin = self._confirm("PIN", "¿Proteger la wallet con PIN?")
        passphrase = self._confirm("Passphrase", "¿Activar passphrase?") if pin else False

        def job(log, progress):
            conn = backend.make_connection()
            try:
                client = self.managed_client(conn)
                if client.features.initialized:
                    backend.device.wipe(backend.seedless_session(client))
                    log("Wallet anterior borrada.", "warn")
                    conn.close()
                    conn = backend.make_connection()
                    client = self.managed_client(conn)
                backend.device.setup(
                    backend.seedless_session(client),
                    strength=strength,
                    backup_type=backup_type,
                    pin_protection=pin,
                    passphrase_protection=passphrase,
                    label=label or None,
                )
            finally:
                conn.close()
            return True

        self.submit(
            job,
            on_result=lambda _: QTimer.singleShot(1200, self.detect),
            busy_text="Creando wallet…",
            timeout_ms=900000,
        )

    def recover_wallet(self):
        if not self._confirm(
            "Recover",
            "Se restaurará una wallet desde la seed. Si ya hay una, se borrará antes.\n"
            "En Safe 3/5 las palabras se escriben en el dispositivo.\n\n¿Continuar?",
        ):
            return
        counts = ["12", "18", "20", "24", "33"]
        picker = ChoiceDialog(self, "Recover", "Número de palabras:", counts, 2)
        if not picker.exec():
            return
        word_count = int(counts[picker.selected()])
        dlg = InputDialog(self, "Nombre", "Nombre del dispositivo:", "Trezor")
        if not dlg.exec():
            return
        label = dlg.value()
        pin = self._confirm("PIN", "¿Proteger la wallet con PIN?")
        passphrase = self._confirm("Passphrase", "¿Activar passphrase?") if pin else False

        def job(log, progress):
            conn = backend.make_connection()
            try:
                client = self.managed_client(conn)
                if client.features.initialized:
                    backend.device.wipe(backend.seedless_session(client))
                    log("Wallet anterior borrada.", "warn")
                    conn.close()
                    conn = backend.make_connection()
                    client = self.managed_client(conn)
                backend.recover_device(
                    backend.seedless_session(client),
                    word_count=word_count,
                    passphrase_protection=passphrase,
                    pin_protection=pin,
                    label=label or None,
                    word_input=self.word_callback,
                )
            finally:
                conn.close()
            return True

        self.submit(
            job,
            on_result=lambda _: QTimer.singleShot(1200, self.detect),
            busy_text="Restaurando wallet…",
            timeout_ms=900000,
        )

    def backup_wallet(self):
        pending = (self.info or {}).get("backup_availability") == int(
            backend.messages.BackupAvailability.Required
        )
        if pending:
            if not self._confirm(
                "Backup pendiente",
                "El dispositivo mostrará la seed en su pantalla.\n"
                "Hazlo en privado y copia todas las palabras. ¿Continuar?",
            ):
                return
            self._run_manage(
                lambda client, log: (
                    backend.device.backup(backend.seedless_session(client)),
                    log("Seed mostrada en el dispositivo. Copia todas las palabras.", "ok"),
                ),
                "Mostrando backup…",
                on_done=lambda: self.set_backup_chip("Backup hecho", "chipOk"),
            )
            return
        if not self._confirm(
            "Comprobar backup",
            "Se comprobará que las palabras de tu papel coinciden con la seed.\n"
            "Las palabras se introducen en el Trezor. La seed no sale del dispositivo.\n\n¿Continuar?",
        ):
            return
        counts = ["12", "18", "20", "24", "33"]
        picker = ChoiceDialog(self, "Backup", "Palabras del backup:", counts, 2)
        if not picker.exec():
            return
        word_count = int(counts[picker.selected()])

        def job(log, progress):
            conn = backend.make_connection()
            try:
                client = self.managed_client(conn)
                backend.recover_device(
                    backend.seedless_session(client),
                    word_count=word_count,
                    passphrase_protection=False,
                    pin_protection=False,
                    label=None,
                    word_input=self.word_callback,
                    dry_run=True,
                )
            finally:
                conn.close()
            return True

        def verified(_):
            self.append_log("Backup verificado.", "ok")
            self.set_backup_chip("Backup verificado", "chipOk")
            QTimer.singleShot(1200, self.detect)

        self.submit(
            job,
            on_result=verified,
            busy_text="Comprobando backup…",
            timeout_ms=900000,
        )


    def change_language(self):
        if self.busy or not self.has_device:
            return
        info = self.info

        def prep(log, progress):
            return backend.fetch_translation_meta(
                info["internal"], info["version"], info["bitcoin_only"], log
            )

        self.submit(
            prep,
            on_result=self.language_choice,
            busy_text="Consultando idiomas…",
            timeout_ms=60000,
        )

    def language_choice(self, meta):
        trans = meta.get("translations") or {}
        if not trans:
            InfoDialog(self, "Idioma", "No hay traducciones para este firmware.").exec()
            return
        codes = list(trans)
        options = [lang_name(code) for code in codes]
        options.append("Inglés (restaurar idioma original)")
        picker = ChoiceDialog(self, "Idioma del dispositivo", "Elige idioma:", options, 0)
        if not picker.exec():
            return
        idx = picker.selected()

        def job(log, progress):
            if idx >= len(codes):
                data = b""
            else:
                code = codes[idx]
                data = backend.download(backend.DATA_BASE + trans[code], log)
            conn = backend.make_connection()
            try:
                client = self.managed_client(conn)
                backend.device.change_language(backend.seedless_session(client), language_data=data)
            finally:
                conn.close()
            return True

        self.submit(
            job,
            on_result=lambda _: QTimer.singleShot(1200, self.detect),
            busy_text="Cambiando idioma…",
            timeout_ms=300000,
        )

    def watch_device(self):
        if self.busy:
            return
        try:
            found = bool(backend.transport.enumerate_devices())
        except Exception:
            found = False
        if not found:
            if self.has_device:
                self.clear_device()
            return
        if self.info is None or self.info.get("locked") or self.info.get("bootloader_mode"):
            self.detect()

    def change_edition(self):
        if not self.has_device or self.busy:
            return
        current = "Solo Bitcoin" if self.edition_btc else "Universal"
        options = ["Universal", "Solo Bitcoin"]
        picker = ChoiceDialog(self, "Edición del firmware", "Elige edición:", options, options.index(current))
        if not picker.exec():
            return
        choice = options[picker.selected()]
        if choice == current:
            return
        want_btc = choice == "Solo Bitcoin"
        if want_btc and self.info and not self.info.get("bitcoin_only"):
            ok = ConfirmDialog(
                self,
                "Edición Bitcoin-only",
                "ATENCIÓN: cambiar de Universal a Bitcoin-only BORRA la wallet del dispositivo.\n\n"
                "Trezor Suite también lo hace así. Necesitarás tu recovery seed para restaurar la wallet.\n"
                "Las apps de otras cadenas dejarán de estar disponibles.",
                confirm="Continuar",
                danger=True,
            ).exec()
            if ok:
                ok = ConfirmDialog(
                    self,
                    "Backup",
                    "¿Tienes tu recovery seed verificada y a mano?\nSi no la tienes, NO continúes.",
                    confirm="Sí, tengo la seed",
                    cancel="Cancelar",
                    danger=True,
                ).exec()
        elif not want_btc and self.info and self.info.get("bitcoin_only"):
            ok = ConfirmDialog(
                self,
                "Edición Universal",
                "ATENCIÓN: cambiar de Bitcoin-only a Universal BORRA la wallet del dispositivo.\n\n"
                "Necesitarás tu recovery seed para restaurar la wallet.\n"
                "Además, puede que el bootloader no acepte Universal en un dispositivo Bitcoin-only; "
                "si lo rechaza, seguirá en bootloader y podrás reinstalar Bitcoin-only.",
                confirm="Continuar",
                danger=True,
            ).exec()
            if ok:
                ok = ConfirmDialog(
                    self,
                    "Backup",
                    "¿Tienes tu recovery seed verificada y a mano?\nSi no la tienes, NO continúes.",
                    confirm="Sí, tengo la seed",
                    cancel="Cancelar",
                    danger=True,
                ).exec()
        else:
            ok = True
        if not ok:
            return
        self.edition_btc = want_btc
        self.edition_value.setText(choice)
        self.refresh_release()

    def refresh_release(self):
        if not self.info or self.busy:
            return
        info = self.info
        btc = bool(self.edition_btc)

        def fn(log, progress):
            releases = backend.fetch_releases(info["internal"])
            return backend.best_release(releases, self.version, info["bootloader_mode"], btc)

        self.submit(
            fn,
            on_result=self.apply_release,
            busy_text="Consultando release…",
            timeout_ms=60000,
        )

    def apply_release(self, rel):
        if not self.info:
            return
        info = self.info
        self.release = rel
        btc = bool(self.edition_btc)
        edition_diff = btc != bool(info.get("bitcoin_only"))
        needs_update = (not info["bootloader_mode"]) and (
            tuple(rel["version"]) > self.version or edition_diff
        )
        self.target_label.setText("v" + ".".join(map(str, rel["version"])))
        self.fw_chip.setText(
            "Cambio de edición"
            if edition_diff and tuple(rel["version"]) <= self.version
            else ("Actualización disponible" if needs_update else ("Bootloader" if info["bootloader_mode"] else "Actualizado"))
        )
        self.fw_chip.setObjectName("chipWarn" if needs_update else "chipOk")
        self.restyle(self.fw_chip)
        if edition_diff:
            self.notes_label.setText("Se instalará la otra edición del firmware (misma versión, edición distinta).")
        elif needs_update:
            self.notes_label.setText("Se reiniciará al bootloader y se subirá el firmware firmado.")
        else:
            self.notes_label.setText("Ya tienes la última versión.")
        self.set_row(self.rel_version, ".".join(map(str, rel["version"])))
        self.set_row(self.rel_edition, "Solo Bitcoin" if btc else "Universal")
        min_v = ".".join(map(str, rel.get("min_firmware_version", [])))
        self.set_row(self.rel_min, min_v or "—")
        fp = backend.release_fingerprint(rel, btc) or ""
        self.set_row(self.rel_fp, fp[:12] + "…" if fp else "—")
        self.apply_buttons()

    # ------------------------------------------------------------------ detection
    def detect(self):
        if self.busy:
            return
        self.append_log("Buscando dispositivo Trezor…", "dim")
        want_btc = self.edition_btc

        def fn(log, progress):
            info, version, bootloader = backend.detect_device(log)
            releases = backend.fetch_releases(info["internal"])
            btc = info["bitcoin_only"] if want_btc is None else want_btc
            rel = backend.best_release(releases, version, bootloader, btc)
            return info, version, bootloader, rel, btc

        self.submit(
            fn,
            on_result=self.apply_detect,
            on_error=lambda e: self.clear_device(),
            busy_text="Buscando dispositivo…",
            timeout_ms=60000,
        )

    def apply_detect(self, result):
        info, version, bootloader, rel, btc = result
        self.info, self.version, self.release = info, version, rel
        self.has_device = True
        self.dev_label.setText(info["name"])
        friendly = (info.get("name") or "Trezor").replace("Trezor ", "").strip()
        self.device_chip.setText(f"{friendly} · v{info['version']}")
        self.update_status()
        self.set_row(self.model_row, info["name"])
        self.set_row(self.fw_row, f"v{info['version']}", ORANGE if not bootloader else None)
        self.set_row(self.mode_row, "Bootloader" if bootloader else "Normal")
        self.set_row(self.vendor_row, info["fw_vendor"])
        if bootloader:
            self.set_row(self.wallet_row, "Bootloader")
            self.set_row(self.pin_row, "—")
            self.set_row(self.pass_row, "—")
            self.set_row(self.lang_row, "—")
            self.backup_chip.hide()
        elif not info["initialized"]:
            self.set_row(self.wallet_row, "Sin crear")
            self.set_row(self.pin_row, "—")
            self.set_row(self.pass_row, "—")
            self.set_row(self.lang_row, lang_name(info.get("language")))
            self.backup_chip.hide()
        else:
            self.set_row(self.wallet_row, "Inicializada")
            self.set_row(self.pin_row, "Bloqueado" if info.get("locked") else ("Activado" if info["pin_protection"] else "Desactivado"), ORANGE if info.get("locked") else (GREEN if info["pin_protection"] else None))
            self.set_row(self.pass_row, "Activada" if info["passphrase_protection"] else "Desactivada")
            backup = int(info.get("backup_availability", -1))
            if backup == 1:
                self.set_backup_chip("Backup pendiente", "chipWarn")
            elif backup == 2:
                self.set_backup_chip("Backup hecho", "chipOk")
            else:
                self.backup_chip.hide()
            self.set_row(self.lang_row, lang_name(info.get("language")))
        edition_diff = self.edition_btc is not None and bool(self.edition_btc) != bool(info.get("bitcoin_only"))
        needs_update = (not bootloader) and (tuple(rel["version"]) > version or edition_diff)
        self.current_label.setText(f"v{info['version']}")
        self.target_label.setText("v" + ".".join(map(str, rel["version"])))
        self.fw_chip.setText("Cambio de edición" if edition_diff and tuple(rel["version"]) <= version else ("Actualización disponible" if needs_update else ("Bootloader" if bootloader else "Actualizado")))
        self.fw_chip.setObjectName("chipWarn" if needs_update else "chipOk")
        self.restyle(self.fw_chip)
        if info.get("locked"):
            self.notes_label.setText("El dispositivo está bloqueado: introduce el PIN en la pantalla del Trezor.")
        elif bootloader:
            self.notes_label.setText("El dispositivo está en modo bootloader; se instalará el firmware directamente.")
        elif not info["initialized"]:
            self.notes_label.setText("Dispositivo sin wallet. Restaura tu wallet desde la recovery seed.")
        elif edition_diff:
            self.notes_label.setText("Se instalará la otra edición del firmware; el dispositivo se borrará.")
        else:
            self.notes_label.setText("Se reiniciará al bootloader y se subirá el firmware firmado.")
        self.edition_btc = bool(btc)
        self.edition_value.setText("Solo Bitcoin" if btc else "Universal")
        self.set_row(self.rel_version, ".".join(map(str, rel["version"])))
        self.set_row(self.rel_edition, "Solo Bitcoin" if btc else "Universal")
        min_v = ".".join(map(str, rel.get("min_firmware_version", [])))
        self.set_row(self.rel_min, min_v or "—")
        fp = backend.release_fingerprint(rel, btc) or ""
        self.set_row(self.rel_fp, fp[:12] + "…" if fp else "—")
        self.apply_buttons()
    # ------------------------------------------------------------------ update
    def _prepare(self, log, progress, btc_pref=None, force_download=False):
        info, version, bootloader = backend.detect_device(log)
        btc = info["bitcoin_only"] if btc_pref is None else btc_pref
        releases = backend.fetch_releases(info["internal"])
        rel = backend.best_release(releases, version, bootloader, btc)
        edition_diff = btc != bool(info.get("bitcoin_only"))
        if not force_download and not bootloader and not edition_diff and tuple(rel["version"]) <= version:
            return {"up_to_date": True, "info": info, "rel": rel, "btc": btc}
        data = backend.download(backend.release_url(rel, btc), log)
        fw, digest = backend.verify_firmware(
            data, backend.release_fingerprint(rel, btc), info["model_names"], log
        )
        return {"up_to_date": False, "info": info, "rel": rel, "btc": btc, "data": data, "fw": fw, "digest": digest}

    def _can_update(self):
        if not self.info or not self.release:
            return False
        if self.info.get("locked"):
            return False
        if self.info["bootloader_mode"]:
            return True
        edition_diff = self.edition_btc is not None and bool(self.edition_btc) != bool(self.info.get("bitcoin_only"))
        return edition_diff or tuple(self.release["version"]) > self.version

    def start_update(self):
        if self.busy:
            return

        btc_pref = self.edition_btc

        def fn(log, progress):
            return self._prepare(log, progress, btc_pref)

        self.submit(fn, on_result=self.confirm_update, busy_text="Preparando firmware…", timeout_ms=300000)

    def confirm_update(self, plan):
        if plan.get("up_to_date"):
            InfoDialog(self, "Trezor", "El dispositivo ya tiene la última versión.").exec()
            return
        rel = plan["rel"]
        info = plan["info"]
        new_v = ".".join(map(str, rel["version"]))
        self._last_edition_change = plan["btc"] != bool(info.get("bitcoin_only"))
        wipe = (
            "ATENCIÓN: cambiar de edición BORRA la wallet del dispositivo.\n"
            "Asegúrate de tener tu recovery seed.\n\n"
            if self._last_edition_change
            else ""
        )
        msg = (
            wipe
            + f"Se instalará el firmware v{new_v} sobre v{info['version']}.\n"
            f"Modelo: {info['name']} ({'solo Bitcoin' if plan['btc'] else 'universal'})\n\n"
            f"Fingerprint:\n{plan['digest']}\n\n"
            "No desconectes el dispositivo durante la subida. ¿Continuar?"
        )
        if not ConfirmDialog(
            self,
            "Actualizar firmware",
            msg,
            confirm="Instalar firmware",
            cancel="Cancelar",
            danger=self._last_edition_change,
        ).exec():
            self.append_log("Actualización cancelada por el usuario.", "warn")
            return

        def fn(log, progress):
            backend.flash(plan["data"], plan["fw"], info["bootloader_onev2"], log, progress)
            return True

        self.submit(fn, on_result=self.update_done, busy_text="Flasheando firmware…", timeout_ms=1800000)

    def update_done(self, _):
        self.progress.setValue(self.progress.maximum())
        self.progress_label.setText("100 %")
        self.append_log("Actualización completada. El dispositivo se reinició.", "ok")
        if getattr(self, "_last_edition_change", False):
            self.append_log(
                "Cambio de edición: la wallet del dispositivo se ha borrado. Restáurala con tu recovery seed.",
                "warn",
            )
        QTimer.singleShot(4000, self.detect)

    def download_only(self):
        if self.busy:
            return

        btc_pref = self.edition_btc

        def fn(log, progress):
            return self._prepare(log, progress, btc_pref, force_download=True)

        self.submit(fn, on_result=self.save_download, busy_text="Descargando firmware…", timeout_ms=300000)

    def save_download(self, plan):
        rel = plan["rel"]
        suggested = backend.release_url(rel, plan["btc"]).rsplit("/", 1)[-1]
        path, _ = QFileDialog.getSaveFileName(self, "Guardar firmware", suggested, "Firmware (*.bin)")
        if not path:
            return
        try:
            Path(path).write_bytes(plan["data"])
            self.append_log(f"Firmware guardado en {path}", "ok")
            self.append_log(f"Fingerprint: {plan['digest']}", "dim")
        except Exception as exc:
            InfoDialog(self, "Guardar firmware", str(exc), error=True).exec()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Actualizador de firmware (Trezor)")
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    install_font(app, _res("common", "fonts", "InterVariable.ttf"))
    win = TrezorWindow()
    win.show()
    QTimer.singleShot(80, lambda: win.resize(1180, 820))
    if "--screenshot" in sys.argv:
        idx = sys.argv.index("--screenshot")
        out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else str(ROOT / "screenshot.png")

        def grab():
            win.grab().save(out)
            app.quit()

        QTimer.singleShot(1400, grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
