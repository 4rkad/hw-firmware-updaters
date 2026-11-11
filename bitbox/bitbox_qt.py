#!/usr/bin/env python3
"""BitBox Firmware Updater — interfaz PySide6 (fase A).

Dispositivo, firmware, actualización, descarga verificada y registro.
La gestión (passphrase, backup, BIP85, etc.) queda para la fase B.
"""

from __future__ import annotations

import html
import importlib.util
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
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
    GREEN,
    LOG_COLORS,
    ORANGE,
    QSS,
    RED,
    ChoiceDialog,
    ConfirmDialog,
    InfoDialog,
    InputDialog,
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


ICON = _res("icons", "bitbox.png")
BACKEND_PATH = _res("bitbox-updater.py") if getattr(sys, "_MEIPASS", None) else ROOT / "bitbox-updater.py"

def _log_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "bitbox-updater.log"



spec = importlib.util.spec_from_file_location("bitbox_backend", BACKEND_PATH)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class PairingDialog(QDialog):
    def __init__(self, parent, code: str):
        super().__init__(parent)
        self.setWindowTitle("Emparejar BitBox")
        self.setStyleSheet(QSS)
        self.setModal(False)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        title = QLabel("Emparejar dispositivo")
        title.setObjectName("h1")
        lay.addWidget(title)
        body = QLabel(
            "Comprueba que este código coincide con el que muestra la pantalla de tu BitBox."
        )
        body.setObjectName("dim")
        body.setWordWrap(True)
        lay.addWidget(body)
        self.code = QLabel(code)
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setStyleSheet(
            f"font-family: 'DejaVu Sans Mono'; font-size: 38px; font-weight: 700;"
            f"color: {ORANGE}; letter-spacing: 10px; padding: 14px 0;"
        )
        lay.addWidget(self.code)
        self.status = QLabel("Esperando confirmación en el dispositivo…")
        self.status.setObjectName("dim")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Cerrar")
        close.setObjectName("secondary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def finish(self, ok: bool):
        self.status.setText(
            "Emparejado correctamente." if ok else "Emparejamiento rechazado o cancelado."
        )
        self.status.setStyleSheet(f"color: {GREEN if ok else RED}; font-weight: 600;")
        QTimer.singleShot(900, self.accept)


class BitBoxWindow(QMainWindow):
    log_requested = Signal(str, str)
    pairing_requested = Signal(str)
    pairing_finished = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Actualizador de firmware · BitBox (no oficial)")
        self.setMinimumSize(980, 620)
        self.resize(1180, 820)
        self.setStyleSheet(QSS)
        self.setWindowIcon(self._icon())
        self.jobs: set[Job] = set()
        self.active_job = None
        self.busy = False
        self.has_device = False
        self.info = None
        self.plan = None
        self.target_v = None
        self.noise = backend.PairingStore()
        self.noise._app = self
        self.log_requested.connect(self.append_log)
        self.pairing_requested.connect(self.show_pairing_dialog)
        self.pairing_finished.connect(self.close_pairing_dialog)
        self.pairing_dlg = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(14)
        outer.addLayout(self.header())
        outer.addWidget(self.content(), 1)
        self.clear_device()
        self.append_log("Buscando dispositivo BitBox…", "dim")
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
        title = QLabel("Actualizador de firmware · BitBox")
        title.setObjectName("title")
        sub = QLabel("Firmware y verificación sin BitBoxApp · proyecto no oficial")
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
        device = self.device_card()
        device.setMinimumHeight(280)
        security = self.security_card()
        security.setMinimumHeight(300)
        firmware = self.firmware_card()
        firmware.setMinimumHeight(280)
        release = self.release_card()
        release.setMinimumHeight(300)
        activity = self.activity_card()
        activity.setMinimumHeight(280)
        manage = self.manage_card()
        manage.setMinimumHeight(300)

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
        self.dev_label = QLabel("Conecta tu BitBox")
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
        self.edition_row = kv("Edición", "—")
        self.wallet_row = kv("Wallet", "—")
        self.pass_row = kv("Passphrase", "—")
        self.name_row = kv("Nombre", "—")
        for r in (self.model_row, self.fw_row, self.mode_row, self.edition_row, self.wallet_row, self.pass_row, self.name_row):
            lay.addLayout(r)
        lay.addStretch(1)
        return frame

    def security_card(self):
        frame, lay = card("SEGURIDAD")
        row = QHBoxLayout()
        row.addWidget(QLabel("Autenticidad"))
        row.addStretch(1)
        self.auth_chip = QLabel("Sin verificar")
        self.auth_chip.setObjectName("chipMuted")
        row.addWidget(self.auth_chip)
        lay.addLayout(row)
        self.btn_auth = QPushButton("Verificar autenticidad")
        self.btn_auth.setObjectName("secondary")
        self.btn_auth.clicked.connect(self.verify_auth)
        lay.addWidget(self.btn_auth, alignment=Qt.AlignLeft)
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
        self.rel_edition = kv("Edición", "—")
        self.rel_release = kv("Release", "—")
        self.rel_chain = kv("Ruta", "—")
        self.rel_hash = kv("Hash de firmware", "—")
        for r in (self.rel_edition, self.rel_release, self.rel_chain, self.rel_hash):
            lay.addLayout(r)
        self.rel_hash.itemAt(2).widget().setToolTip(
            "Huella del firmware que el bootloader puede mostrar en pantalla tras el flasheo. "
            "Compárala con la publicada en el release de GitHub."
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
            ("Passphrase", self.toggle_passphrase),
            ("Backup microSD", self.check_backup),
            ("Mostrar seed", self.show_mnemonic),
            ("Crear wallet", self.create_wallet),
            ("Restaurar backup", self.restore_backup),
            ("Renombrar", self.rename_device),
            ("BIP85", self.bip85),
            ("Cambiar contraseña", self.change_password),
            ("Borrar dispositivo", self.wipe_device),
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
        self.append_log(error, "error")
        self.set_busy(False)
        self.status_chip.setText("● Error")
        self.status_chip.setObjectName("chipBad")
        self.restyle(self.status_chip)
        if callback:
            callback(error)
        else:
            InfoDialog(self, "BitBox Updater", error, error=True).exec()

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
        blocked = info.get("initialized") is None
        base = not busy and self.has_device and info.get("mode") == "normal" and not blocked
        self.btn_auth.setEnabled(base)
        for b in getattr(self, "manage_btns", []):
            label = b.text()
            if label in ("Crear wallet", "Restaurar backup", "Renombrar"):
                b.setEnabled(base)
            else:
                b.setEnabled(base and initialized)

    def restyle(self, w):
        w.style().unpolish(w)
        w.style().polish(w)

    def update_status(self):
        if not self.info:
            text, name = "● Sin dispositivo", "chipMuted"
        elif self.info.get("mode") == "bootloader":
            text, name = "● Bootloader", "chipWarn"
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
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{tag or 'info'}] {msg}\\n")
        except Exception:
            pass

    def set_progress(self, cur, total):
        total = max(int(total or 0), 1)
        self.progress.setMaximum(total)
        self.progress.setValue(int(cur))
        self.progress_label.setText(f"{int(cur * 100 / total)} %")

    def show_log(self):
        TextDialog(self, "Registro", self.logbox.toPlainText()).exec()

    @staticmethod
    def ver(value):
        return str(value or "").strip().lstrip("vV")

    def vlabel(self, value):
        value = self.ver(value)
        return f"v{value}" if value else "—"

    def set_row(self, row, value, color=None):
        if row is not None and row.count() >= 3:
            w = row.itemAt(2).widget()
            w.setText(value)
            w.setStyleSheet(f"color: {color}; font-weight: 600;" if color else "")

    # ------------------------------------------------------------------ pairing
    def log(self, msg, tag=None):
        self.log_requested.emit(msg, tag or "")

    def show_pairing_dialog(self, code: str):
        if self.pairing_dlg is not None:
            self.pairing_dlg.accept()
        self.pairing_dlg = PairingDialog(self, code)
        self.pairing_dlg.show()
        self.pairing_dlg.raise_()

    def close_pairing_dialog(self, ok: bool):
        if self.pairing_dlg is not None:
            self.pairing_dlg.finish(ok)
            self.pairing_dlg = None

    def pairing_flow(self, code, device_response):
        self.log_requested.emit(f"Código de emparejamiento: {code}", "accent")
        self.pairing_requested.emit(code)
        try:
            ok = bool(device_response())
        except Exception as exc:
            self.log_requested.emit(f"Emparejamiento interrumpido: {exc}", "warn")
            ok = False
        self.pairing_finished.emit(ok)
        self.log_requested.emit(
            "Emparejamiento confirmado en el dispositivo." if ok else "Emparejamiento rechazado en el dispositivo.",
            "ok" if ok else "warn",
        )
        return ok

    def clear_device(self):
        self.has_device = False
        self.info = None
        self.plan = None
        self.target_v = None
        self.dev_label.setText("Sin dispositivo")
        self.device_chip.setText("")
        for row in (
            self.model_row, self.fw_row, self.mode_row, self.edition_row,
            self.wallet_row, self.pass_row, self.name_row,
            self.rel_edition, self.rel_release, self.rel_chain, self.rel_hash,
        ):
            self.set_row(row, "—")
        self.current_label.setText("—")
        self.target_label.setText("—")
        self.notes_label.setText("Conecta el dispositivo para consultar la versión.")
        self.fw_chip.setText("Sin dispositivo")
        self.fw_chip.setObjectName("chipMuted")
        self.backup_chip.hide()
        self.auth_chip.setText("Sin verificar")
        self.auth_chip.setObjectName("chipMuted")
        self.restyle(self.auth_chip)
        self.restyle(self.fw_chip)
        self.update_status()
        self.apply_buttons()


    # ------------------------------------------------------------------ gestión
    def _with_client(self, fn, log):
        normals, _ = backend.usb_scan()
        if not normals:
            raise RuntimeError("No hay BitBox en modo normal conectada")
        info = normals[0]
        # La librería atestigua en cada conexión; en acciones rutinarias no hace
        # falta repetirlo en el registro. Un fallo sí se muestra siempre.
        self.noise.quiet_attestation = True
        client = backend.make_client(info, self.noise, log)
        try:
            try:
                return fn(client, log)
            except backend.Bitbox02Exception as exc:
                if "wrong state" not in str(exc).lower():
                    raise
                log("Dispositivo bloqueado — reconectando para pedir la contraseña…", "warn")
                client.close()
                normals, _ = backend.usb_scan()
                if not normals:
                    raise RuntimeError("El dispositivo desapareció") from exc
                info = normals[0]
                client = backend.make_client(info, self.noise, log, unlock_first=True)
                return fn(client, log)
        finally:
            self.noise.quiet_attestation = False
            try:
                client.close()
            except Exception:
                pass

    def _mgmt(self, fn, busy_text, confirm=None, danger=False, on_done=None):
        if self.busy or not self.has_device:
            return
        if confirm and not ConfirmDialog(
            self, "BitBox", confirm, confirm="Continuar", cancel="Cancelar", danger=danger
        ).exec():
            return

        def job(log, progress):
            self.noise._last_attestation = None
            self._with_client(fn, log)
            return True

        def done(_):
            if self.noise._last_attestation is True:
                self.auth_chip.setText("Genuino")
                self.auth_chip.setObjectName("chipOk")
                self.restyle(self.auth_chip)
            if on_done:
                on_done()
            QTimer.singleShot(1200, self.detect)

        self.submit(job, on_result=done, busy_text=busy_text, timeout_ms=900000)

    def toggle_passphrase(self):
        def fn(client, log):
            di = client.device_info()
            if di["mnemonic_passphrase_enabled"]:
                client.disable_mnemonic_passphrase()
                log("Passphrase desactivada.", "ok")
            else:
                client.enable_mnemonic_passphrase()
                log("Passphrase activada.", "ok")

        self._mgmt(fn, "Cambiando passphrase…", "Se cambiará el ajuste de passphrase. Confirma en el dispositivo.")

    def check_backup(self):
        def fn(client, log):
            result = client.check_backup(silent=False)
            if result is None:
                log("No hay backup en la microSD o no coincide.", "warn")
            else:
                log(f"Backup verificado: {result}", "ok")

        self._mgmt(fn, "Verificando backup…")

    def show_mnemonic(self):
        self._mgmt(
            lambda client, log: (client.show_mnemonic(), log("Seed mostrada en el dispositivo.", "ok")),
            "Mostrando seed…",
            "MOSTRAR LA SEED en la pantalla del dispositivo.\n\n"
            "Asegúrate de que nadie te observa ni hay cámaras. ¿Continuar?",
            danger=True,
        )

    def wipe_device(self):
        def fn(client, log):
            if not client.reset():
                raise RuntimeError("El borrado fue cancelado o falló")
            log("Dispositivo borrado — como nuevo.", "ok")

        self._mgmt(
            fn,
            "Borrando dispositivo…",
            "BORRAR EL DISPOSITIVO (factory reset).\n\n"
            "La seed se elimina y no se puede recuperar sin tu backup físico.\n"
            "El nombre y los ajustes también se pierden. ¿Borrar?",
            danger=True,
        )

    def create_wallet(self):
        if self.busy or not self.has_device:
            return
        picker = ChoiceDialog(
            self, "Crear wallet", "Tamaño de la seed:", ["24 palabras", "12 palabras"], 0
        )
        if not picker.exec():
            return
        size = 16 if picker.selected() == 1 else 32

        def fn(client, log):
            log("Sigue las instrucciones en la pantalla del dispositivo…", "dim")
            while not client.set_password(entropy_size=size):
                log("Las contraseñas no coincidieron — reinténtalo en el dispositivo.", "warn")
            log("Seed creada y contraseña definida.", "ok")

        self._mgmt(
            fn,
            "Creando wallet…",
            "CREAR WALLET NUEVA.\n\n"
            "El dispositivo generará una seed y pedirá una contraseña.\n"
            "Haz y verifica el backup antes de enviar fondos. ¿Continuar?",
            danger=True,
            on_done=self.after_create,
        )

    def after_create(self):
        picker = ChoiceDialog(
            self,
            "Backup",
            "Haz y verifica el backup ANTES de enviar fondos. ¿Cómo?",
            ["Backup a microSD", "Mostrar palabras en pantalla", "Ahora no"],
            0,
        )
        if not picker.exec() or picker.selected() == 2:
            self.append_log(
                "SIN BACKUP: la wallet solo vive en el dispositivo. Hazlo cuanto antes.",
                "warn",
            )
            return
        if picker.selected() == 0:
            self._mgmt(
                lambda client, log: client.create_backup() or (_ for _ in ()).throw(RuntimeError("La creación del backup falló")),
                "Creando backup…",
                on_done=lambda: self.append_log("Backup creado en la microSD.", "ok"),
            )
        else:
            self._mgmt(
                lambda client, log: (client.show_mnemonic(), log("Palabras mostradas; cópialas y verifica.", "ok")),
                "Mostrando palabras…",
            )

    def restore_backup(self):
        if self.busy or not self.has_device:
            return

        def job(log, progress):
            backups = []
            self._with_client(lambda client, info: backups.extend(client.list_backups()), log)
            return backups

        self.submit(job, on_result=self.choose_restore, busy_text="Leyendo backups…", timeout_ms=120000)

    def choose_restore(self, backups):
        if not backups:
            self.append_log("No hay backups en la microSD.", "warn")
            return
        labels = [f"{name or 'backup'} · {date:%Y-%m-%d %H:%M}" for _, name, date in backups]
        picker = ChoiceDialog(self, "Restaurar backup", "Elige el backup:", labels, 0)
        if not picker.exec():
            return
        backup_id = backups[picker.selected()][0]

        def fn(client, log):
            try:
                client.restore_backup(backup_id)
            except OSError:
                pass
            log("Backup restaurado — retira la microSD.", "ok")

        self._mgmt(fn, "Restaurando backup…")

    def rename_device(self):
        if self.busy or not self.has_device:
            return
        current = (self.info or {}).get("name") or ""
        dlg = InputDialog(self, "Renombrar dispositivo", "Nombre nuevo (1-64 caracteres):", current)
        if not dlg.exec():
            return
        name = dlg.value().strip()
        if not name or len(name) > 64:
            InfoDialog(self, "Nombre", "El nombre debe tener entre 1 y 64 caracteres.", error=True).exec()
            return
        self._mgmt(
            lambda client, log: (client.set_device_name(name), log(f"Dispositivo renombrado a «{name}».", "ok")),
            "Renombrando…",
        )

    def bip85(self):
        if self.busy or not self.has_device:
            return
        self._mgmt(
            lambda client, log: (
                client.bip85_bip39(),
                log("Palabras BIP85 mostradas en la pantalla de la BitBox — apúntalas ahora.", "ok"),
            ),
            "Generando BIP85…",
        )

    def change_password(self):
        def fn(client, log):
            if not client.set_password():
                raise RuntimeError("Las contraseñas no coincidieron — reinténtalo")
            log("Contraseña cambiada — la seed sigue intacta.", "ok")

        self._mgmt(
            fn,
            "Cambiando contraseña…",
            "CAMBIAR LA CONTRASEÑA DEL DISPOSITIVO.\n\n"
            "La seed no cambia: solo se re-encripta. Introducirás la nueva dos veces en el dispositivo.",
        )

    def verify_auth(self):
        if self.busy or not self.has_device:
            return
        self.noise._last_attestation = None

        def job(log, progress):
            self._with_client(lambda client, info: None, log)
            return self.noise._last_attestation

        def done(result):
            if result is True:
                self.auth_chip.setText("Genuino")
                self.auth_chip.setObjectName("chipOk")
                self.append_log("DISPOSITIVO GENUINO — atestación verificada.", "ok")
            else:
                self.auth_chip.setText("No genuino")
                self.auth_chip.setObjectName("chipBad")
                self.append_log("ATESTACIÓN FALLIDA — no uses este dispositivo con fondos reales.", "error")
            self.restyle(self.auth_chip)
            QTimer.singleShot(1200, self.detect)

        self.submit(job, on_result=done, busy_text="Verificando autenticidad…", timeout_ms=300000)

    # ------------------------------------------------------------------ detection
    def watch_device(self):
        if self.busy:
            return
        try:
            normals, bootloaders = backend.usb_scan()
        except Exception:
            normals, bootloaders = [], []
        if not normals and not bootloaders:
            if self.has_device:
                self.clear_device()
            return
        if (
            self.info is None
            or self.info.get("mode") == "bootloader"
            or self.info.get("initialized") is None
        ):
            self.detect(quiet=True)

    def detect(self, quiet=False):
        if self.busy:
            return
        if not quiet:
            self.append_log("Buscando dispositivo BitBox…", "dim")
        noise = self.noise

        def fn(log, progress):
            normals, bootloaders = backend.usb_scan()
            if normals:
                info = normals[0]
                platform, edition = backend.NORMAL_IDS[info["product_string"]]
                version = str(backend.bbdevices.parse_device_version(info["serial_number"]))
                mode = "normal"
            elif bootloaders:
                info = bootloaders[0]
                platform, edition = backend.BOOTLOADER_IDS[info["product_string"]]
                version = None
                mode = "bootloader"
            else:
                return None
            release = asset = None
            target = dev_hash = None
            try:
                release = backend.fetch_latest_release()
                asset = backend.pick_asset(release, platform, edition)
                target = backend.ASSET_RE.search(asset["name"]).group(3) if asset else None
                dev_hash = backend.parse_device_hashes(release.get("body") or "").get(
                    backend.EDITION_LABEL[(platform, edition)]
                )
            except Exception as exc:
                if not quiet:
                    log(f"No se pudo consultar el último firmware: {exc}", "warn")
            log(
                f"BitBox detectada: {backend.EDITION_LABEL[(platform, edition)]} · "
                f"{'bootloader' if mode == 'bootloader' else 'v' + str(version)} · "
                f"target {target or '—'}"
            )
            initialized = passphrase = name = None
            attestation = None
            if mode == "normal":
                noise.quiet_attestation = True
                noise._last_attestation = None
                try:
                    client = backend.make_client(info, noise, log)
                    di = client.device_info()
                    initialized = di.get("initialized")
                    passphrase = di.get("mnemonic_passphrase_enabled")
                    name = di.get("name")
                    version = self.ver(di.get("version") or version)
                    client.close()
                except Exception as exc:
                    if not quiet:
                        log(f"No se pudo leer el estado: {exc}", "warn")
                finally:
                    noise.quiet_attestation = False
                    attestation = noise._last_attestation
            return {
                "mode": mode, "platform": platform, "edition": edition,
                "version": version, "info": info, "initialized": initialized,
                "passphrase": passphrase, "name": name, "target": target,
                "release_name": release.get("name") if release else None,
                "asset": asset, "dev_hash": dev_hash,
                "release_ok": release is not None,
                "attestation": attestation,
            }

        self.submit(
            fn,
            on_result=self.apply_detect,
            on_error=lambda e: self.clear_device(),
            busy_text="Buscando dispositivo…",
            timeout_ms=90000,
        )

    def apply_detect(self, data):
        if not data:
            self.clear_device()
            return
        self.info = data
        self.has_device = True
        if data.get("attestation") is True:
            self.auth_chip.setText("Genuino")
            self.auth_chip.setObjectName("chipOk")
            self.restyle(self.auth_chip)
        elif data.get("attestation") is False:
            self.auth_chip.setText("No genuino")
            self.auth_chip.setObjectName("chipBad")
            self.restyle(self.auth_chip)
        label = backend.EDITION_LABEL[(data["platform"], data["edition"])]
        self.dev_label.setText(label)
        self.device_chip.setText(
            f"{label} · {self.vlabel(data['version'])}" if data.get("version") else f"{label} · bootloader"
        )
        self.update_status()
        self.set_row(self.model_row, label)
        self.set_row(self.fw_row, self.vlabel(data.get("version")), ORANGE if data.get("version") else None)
        self.set_row(self.mode_row, "Bootloader" if data["mode"] == "bootloader" else "Normal")
        self.set_row(self.edition_row, "Solo Bitcoin" if data["edition"] == "btconly" else "Multi")
        blocked = data["mode"] == "normal" and data.get("initialized") is None
        if blocked:
            self.set_row(self.wallet_row, "Bloqueado", ORANGE)
            self.set_row(self.pass_row, "—")
            self.set_row(self.name_row, "—")
        else:
            self.set_row(self.wallet_row, "Inicializada" if data.get("initialized") else "Sin crear")
            self.set_row(self.pass_row, "Activada" if data.get("passphrase") else ("Desactivada" if data.get("passphrase") is not None else "—"))
            self.set_row(self.name_row, data.get("name") or "—")
        self.current_label.setText(self.vlabel(data.get("version")) if data.get("version") else "bootloader")
        self.target_label.setText(self.vlabel(data.get("target")) if data.get("target") else "—")
        self.set_row(self.rel_edition, "Solo Bitcoin" if data["edition"] == "btconly" else "Multi")
        self.set_row(self.rel_release, data.get("release_name") or "—")
        self.set_row(self.rel_chain, self.vlabel(data.get("target")) if data.get("target") else "—")
        dev_hash = data.get("dev_hash") or ""
        self.set_row(self.rel_hash, (dev_hash[:16] + "…") if dev_hash else "—")
        self.rel_hash.itemAt(2).widget().setToolTip(dev_hash or "El release no publica hash de firmware.")
        if blocked:
            self.notes_label.setText("Desbloquea la BitBox en el dispositivo para leer el estado de la wallet.")
        elif data["mode"] == "bootloader":
            self.notes_label.setText("El dispositivo está en bootloader; se instalará el firmware directamente.")
        else:
            self.notes_label.setText("Se reiniciará al bootloader y se flashearán los binarios firmados.")
        if data.get("release_ok"):
            self._release_warned = False
        if not data.get("release_ok"):
            self.fw_chip.setText("Sin conexión a GitHub")
            self.fw_chip.setObjectName("chipWarn")
            if not getattr(self, "_release_warned", False):
                self._release_warned = True
                self.append_log(
                    "No se pudo consultar el último firmware (GitHub no responde o límite de peticiones). "
                    "Se muestra solo el estado del dispositivo.",
                    "warn",
                )
        elif not data.get("target"):
            self.fw_chip.setText("Sin release compatible")
            self.fw_chip.setObjectName("chipBad")
            self.append_log("No hay asset compatible para esta edición en el último release.", "warn")
        elif data["mode"] == "bootloader":
            self.fw_chip.setText("Bootloader")
            self.fw_chip.setObjectName("chipWarn")
        else:
            try:
                cur = backend.semver.VersionInfo.parse(self.ver(data["version"]))
                tgt = backend.semver.VersionInfo.parse(self.ver(data["target"]))
                if tgt > cur:
                    self.fw_chip.setText("Actualización disponible")
                    self.fw_chip.setObjectName("chipWarn")
                else:
                    self.fw_chip.setText("Actualizado")
                    self.fw_chip.setObjectName("chipOk")
            except Exception as exc:
                self.append_log(f"No se pudo comparar la versión {data.get('version')!r}: {exc}", "warn")
                self.fw_chip.setText("Versión desconocida")
                self.fw_chip.setObjectName("chipBad")
        self.restyle(self.fw_chip)
        self.apply_buttons()

    def _can_update(self):
        if not self.info:
            return False
        if self.info["mode"] == "bootloader":
            return bool(self.info.get("target"))
        if self.info.get("initialized") is None:
            return False
        try:
            cur = backend.semver.VersionInfo.parse(self.info["version"])
            tgt = backend.semver.VersionInfo.parse(self.info["target"])
            return tgt > cur
        except Exception:
            return False

    # ------------------------------------------------------------------ update
    def _plan(self, log, progress):
        normals, bootloaders = backend.usb_scan()
        if normals:
            info = normals[0]
            platform, edition = backend.NORMAL_IDS[info["product_string"]]
            current = backend.bbdevices.parse_device_version(info["serial_number"])
            mode = "normal"
        elif bootloaders:
            info = bootloaders[0]
            platform, edition = backend.BOOTLOADER_IDS[info["product_string"]]
            current = None
            mode = "bootloader"
        else:
            raise RuntimeError("No se detectó ningún BitBox (ni firmware ni bootloader)")

        latest = backend.fetch_json(backend.GITHUB_API + "/releases/latest")
        asset = backend.pick_asset(latest, platform, edition)
        if not asset:
            raise RuntimeError("No hay asset para esta edición en el último release")
        target_v = backend.ASSET_RE.search(asset["name"]).group(3)
        if mode == "normal" and current is not None and backend.semver.VersionInfo.parse(target_v) <= current:
            return {"up_to_date": True, "target": target_v, "info": info, "platform": platform, "edition": edition, "mode": mode}

        steps = backend.upgrade_path(current, latest.get("body") or "") if current is not None else []
        images = []
        for step_v in steps:
            rel = backend.fetch_json(backend.RELEASE_BY_TAG.format(step_v))
            a = backend.pick_asset(rel, platform, edition)
            if not a:
                raise RuntimeError(f"No hay asset v{step_v} para esta edición")
            images.append((step_v, a, rel))
        images.append((target_v, asset, latest))
        dev_hash = backend.parse_device_hashes(latest.get("body") or "").get(
            backend.EDITION_LABEL[(platform, edition)]
        )
        return {
            "up_to_date": False, "info": info, "platform": platform, "edition": edition,
            "mode": mode, "current": current, "target": target_v, "images": images,
            "dev_hash": dev_hash, "release_name": latest.get("name"),
        }

    def start_update(self):
        if self.busy:
            return
        self.submit(lambda log, progress: self._plan(log, progress), on_result=self.confirm_update, busy_text="Preparando firmware…", timeout_ms=120000)

    def confirm_update(self, plan):
        if not plan:
            return
        if plan.get("up_to_date"):
            InfoDialog(self, "BitBox", "El dispositivo ya tiene la última versión.").exec()
            return
        chain = " → ".join(v for v, _, _ in plan["images"])
        edition = backend.EDITION_LABEL[(plan["platform"], plan["edition"])]
        dev_hash = plan.get("dev_hash") or ""
        self.set_row(self.rel_hash, (dev_hash[:16] + "…") if dev_hash else "—")
        self.rel_hash.itemAt(2).widget().setToolTip(dev_hash or "El release no publica hash de firmware.")
        msg = (
            f"Se instalará la ruta: {chain}\\n"
            f"Edición: {edition}\\n"
            f"Origen: GitHub de BitBoxSwiss (SHA-256 y magic verificados)\\n"
            + (f"Hash que mostrará el dispositivo: {plan['dev_hash']}\\n" if plan.get("dev_hash") else "")
            + "\\nNo desconectes el dispositivo durante el flasheo."
        )
        if not ConfirmDialog(self, "Actualizar firmware", msg, confirm="Instalar firmware").exec():
            self.append_log("Actualización cancelada por el usuario.", "warn")
            return
        self.submit(lambda log, progress: self._flash(plan, log, progress), on_result=self.update_done, busy_text="Flasheando firmware…", timeout_ms=1800000)

    def _flash(self, plan, log, progress):
        platform = plan["platform"]
        edition = plan["edition"]
        mode = plan["mode"]
        info = plan["info"]
        dev_hash = plan.get("dev_hash")
        blobs = []
        for v, a, _ in plan["images"]:
            data = backend.download(a["browser_download_url"], log)
            backend.verify_blob(data, a, platform, edition, log)
            blobs.append((v, data))

        for i, (v, data) in enumerate(blobs):
            final = i == len(blobs) - 1
            if mode == "normal":
                log(f"Reiniciando a bootloader (v{v})… confirma en el dispositivo")
                client = backend.make_client(info, self.noise, log)
                try:
                    client.reboot()
                finally:
                    client.close()
                backend.wait_for(
                    lambda: bool(backend.bbdevices.get_any_bitbox02_bootloaders()),
                    90, log, "dispositivo en modo bootloader",
                )
                mode = "bootloader"
            bl_info = backend.bbdevices.get_any_bitbox02_bootloader()
            if not bl_info:
                raise RuntimeError("No se encontró el bootloader de la BitBox")
            bl = backend.Bootloader(backend.hid_transport(bl_info), bl_info)
            try:
                log(f"Flasheando v{v}…")
                bl.flash_signed_firmware(data, progress_callback=lambda p: progress(int(p * 100), 100))
                if final and dev_hash:
                    log(f"Hash esperado del firmware: {dev_hash}", "dim")
                    bl.get_hashes(display_firmware_hash=True, display_signing_keydata_hash=True)
                    log("Hash mostrado en pantalla; compáralo con el esperado", "accent")
                log("Reiniciando…")
                bl.reboot()
            finally:
                try:
                    bl.close()
                except Exception:
                    pass
            if not final:
                backend.wait_for(
                    lambda: bool(backend.bbdevices.get_any_bitbox02s()),
                    90, log, "dispositivo arranque con firmware intermedio",
                )
                normals = backend.bbdevices.get_any_bitbox02s()
                info = normals[0]
                mode = "normal"
        return plan["target"]

    def update_done(self, target):
        self.progress.setValue(self.progress.maximum())
        self.progress_label.setText("100 %")
        self.append_log(f"Actualización completada: v{target}.", "ok")
        QTimer.singleShot(4000, self.detect)

    def download_only(self):
        if self.busy:
            return

        def fn(log, progress):
            plan = self._plan(log, progress)
            if plan.get("up_to_date"):
                raise RuntimeError("Ya tienes la última versión")
            data = backend.download(plan["images"][-1][1]["browser_download_url"], log)
            backend.verify_blob(data, plan["images"][-1][1], plan["platform"], plan["edition"], log)
            return data, plan["images"][-1][1]["name"]

        self.submit(fn, on_result=self.save_download, busy_text="Descargando firmware…", timeout_ms=300000)

    def save_download(self, result):
        data, name = result
        path, _ = QFileDialog.getSaveFileName(self, "Guardar firmware", name, "Firmware (*.bin)")
        if not path:
            return
        try:
            Path(path).write_bytes(data)
            self.append_log(f"Firmware guardado en {path}", "ok")
        except Exception as exc:
            InfoDialog(self, "Guardar firmware", str(exc), error=True).exec()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Actualizador de firmware (BitBox)")
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    install_font(app, _res("common", "fonts", "InterVariable.ttf"))
    win = BitBoxWindow()
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
