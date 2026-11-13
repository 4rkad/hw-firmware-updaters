#!/usr/bin/env python3
"""Ledger Firmware Updater — interfaz PySide6.

Capa visual sobre el backend de `ledger-updater.py`. No duplica la lógica de
flasheo: importa las funciones existentes y las ejecuta en hilos de trabajo.
"""

from __future__ import annotations

import html
import importlib.util
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parent
def _res(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


ICON = _res("icons", "ledger.png")
BACKEND_PATH = _res("ledger-updater.py") if getattr(sys, "_MEIPASS", None) else ROOT / "ledger-updater.py"

spec = importlib.util.spec_from_file_location("ledger_backend", BACKEND_PATH)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))

from ui_common import ConfirmDialog, InfoDialog, TextDialog

ORANGE = "#f8b21c"
GREEN = "#66bb6a"
RED = "#ef5350"
DIM = "#8f8f8f"
LOG_COLORS = {"": "#b8b8b8", "ok": GREEN, "warn": ORANGE, "error": RED, "accent": ORANGE, "dim": DIM}

QSS = f"""
QWidget {{ background: #111111; color: #e7e7e7; font-size: 13px; }}
QLabel, QCheckBox {{ background: transparent; }}
QFrame#card {{ background: #191919; border: 1px solid #2b2b2b; border-radius: 16px; }}
QLabel#title {{ font-size: 26px; font-weight: 700; }}
QLabel#h1 {{ font-size: 17px; font-weight: 700; }}
QLabel#h2 {{ font-size: 13px; font-weight: 700; color: #cfcfcf; }}
QLabel#dim {{ color: {DIM}; }}
QLabel#big {{ font-size: 22px; font-weight: 700; }}
QLabel#mono {{ font-family: "DejaVu Sans Mono"; font-size: 11px; }}
QLabel#chipOk {{
    background: rgba(102, 187, 106, 0.14); color: #86d98a;
    border: 1px solid rgba(102, 187, 106, 0.35); border-radius: 10px;
    padding: 4px 10px; font-weight: 600;
}}
QLabel#chipWarn {{
    background: rgba(248, 178, 28, 0.14); color: {ORANGE};
    border: 1px solid rgba(248, 178, 28, 0.35); border-radius: 10px;
    padding: 4px 10px; font-weight: 600;
}}
QLabel#chipMuted {{
    background: #222222; color: #a7a7a7; border: 1px solid #333333;
    border-radius: 10px; padding: 4px 10px;
}}
QLabel#chipBad {{
    background: rgba(239, 83, 80, 0.14); color: #ef8a88;
    border: 1px solid rgba(239, 83, 80, 0.35); border-radius: 10px;
    padding: 4px 10px; font-weight: 600;
}}
QLabel#chipOk, QLabel#chipWarn, QLabel#chipMuted, QLabel#chipBad {{ min-height: 18px; }}
QPushButton#primary {{
    background: {ORANGE}; color: #171717; border: none; border-radius: 10px;
    padding: 10px 18px; font-weight: 700;
}}
QPushButton#primary:hover {{ background: #ffcb57; }}
QPushButton#primary:disabled {{ background: #5a4a1e; color: #8a7a4a; }}
QPushButton#secondary {{
    background: #242424; color: #e0e0e0; border: 1px solid #333333;
    border-radius: 10px; padding: 9px 15px;
}}
QPushButton#secondary:hover {{ background: #2f2f2f; border-color: #444444; }}
QPushButton#secondary:disabled {{ background: #1b1b1b; color: #5f5f5f; border-color: #262626; }}
QPushButton#ghost {{
    background: transparent; color: #bdbdbd; border: none;
    padding: 6px 4px; text-align: left;
}}
QPushButton#ghost:hover {{ color: {ORANGE}; }}
QCheckBox {{ spacing: 8px; color: #cfcfcf; }}
QCheckBox::indicator {{
    width: 17px; height: 17px; border-radius: 5px;
    border: 1px solid #3a3a3a; background: #1f1f1f;
}}
QCheckBox::indicator:checked {{ background: {ORANGE}; border-color: {ORANGE}; }}
QComboBox {{
    background: #151515; border: 1px solid #2e2e2e; border-radius: 9px;
    padding: 7px 10px; color: #e7e7e7;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #1b1b1b; border: 1px solid #333333; color: #e7e7e7;
    selection-background-color: {ORANGE}; selection-color: #171717;
}}
QProgressBar {{
    background: #0d0d0d; border: 1px solid #262626; border-radius: 5px;
    min-height: 10px; max-height: 10px;
}}
QProgressBar::chunk {{ background: {ORANGE}; border-radius: 5px; }}
QTextEdit#log {{
    background: #0d0d0d; border: 1px solid #262626; border-radius: 12px;
    color: #b8b8b8; font-family: "DejaVu Sans Mono"; font-size: 11px; padding: 10px;
}}
QScrollArea {{ border: none; background: transparent; }}
QLineEdit {{
    background: #151515; border: 1px solid #2e2e2e; border-radius: 10px;
    padding: 9px 12px; color: #e7e7e7; selection-background-color: #3a3a3a;
}}
QLineEdit:focus {{ border-color: {ORANGE}; }}
QTableWidget {{
    background: #151515; border: 1px solid #262626; border-radius: 12px;
    gridline-color: transparent; selection-background-color: #242424;
    selection-color: #ffffff;
}}
QTableWidget::item {{ border-bottom: 1px solid #222222; padding: 6px 8px; }}
QHeaderView::section {{
    background: #191919; color: #8f8f8f; border: none;
    border-bottom: 1px solid #2b2b2b; padding: 10px 8px;
    font-size: 10px; font-weight: 700;
}}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333333; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def round_pixmap(path: Path, size: int) -> QPixmap:
    src = QPixmap(str(path))
    if src.isNull():
        src = QPixmap(size, size)
        src.fill(QColor("#222222"))
    src = src.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path_ = QPainterPath()
    path_.addRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    p.setClipPath(path_)
    p.drawPixmap(0, 0, src)
    p.end()
    return out


def card(title: str | None = None):
    frame = QFrame()
    frame.setObjectName("card")
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(10)
    if title:
        label = QLabel(title)
        label.setObjectName("h2")
        lay.addWidget(label)
    return frame, lay


def kv(label: str, value: str, color: str | None = None):
    row = QHBoxLayout()
    row.setSpacing(8)
    lbl = QLabel(label)
    lbl.setObjectName("dim")
    v = QLabel(value)
    v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        v.setStyleSheet(f"color: {color}; font-weight: 600;")
    row.addWidget(lbl)
    row.addStretch(1)
    row.addWidget(v)
    return row


class Job(QThread):
    log = Signal(str, str)
    progress = Signal(int, int)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            out = self.fn(
                lambda m, t=None: self.log.emit(m, t or ""),
                lambda c, t: self.progress.emit(c, t),
            )
            self.result.emit(out)
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Actualizador de firmware · Ledger (no oficial)")
        self.setMinimumSize(980, 620)
        self.resize(1180, 820)
        self.setStyleSheet(QSS)
        self.setWindowIcon(QIcon(str(ICON)))

        self.jobs: set[Job] = set()
        self.active_job = None
        self.busy = False
        self.path = None
        self.devices = []
        self.dev_paths = []
        self.info = None
        self.plan = None
        self.catalog = []
        self.installed = []
        self.meta = []
        self.langs = []
        self.update_names = []
        self.offline = False
        self.has_device = False
        self.can_update = False

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(14)
        outer.addLayout(self.header())
        outer.addWidget(self.content(), 1)
        self.clear_device()
        self.append_log("Buscando dispositivos Ledger…", "dim")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.watch_devices)
        self.timer.start(2500)
        QTimer.singleShot(200, self.refresh_devices)

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
        title = QLabel("Actualizador de firmware · Ledger")
        title.setObjectName("title")
        sub = QLabel("Firmware, apps e idioma sin Ledger Wallet · proyecto no oficial")
        sub.setObjectName("dim")
        titles.addWidget(title)
        titles.addWidget(sub)
        row.addLayout(titles)
        row.addStretch(1)

        self.offline_cb = QCheckBox("Offline")
        self.offline_cb.toggled.connect(self.toggle_offline)
        row.addWidget(self.offline_cb)

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
        apps = self.apps_card()
        apps.setMinimumHeight(300)
        activity = self.activity_card()
        activity.setMinimumHeight(280)
        language = self.language_card()
        language.setMinimumHeight(300)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(12)
        grid.setHorizontalSpacing(14)
        grid.addWidget(device, 0, 0)
        grid.addWidget(security, 1, 0)
        grid.addWidget(firmware, 0, 1)
        grid.addWidget(apps, 1, 1)
        grid.addWidget(activity, 0, 2)
        grid.addWidget(language, 1, 2)
        grid.addWidget(self.preferences_card(), 2, 0, 1, 3)
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
        self.dev_combo = QComboBox()
        self.dev_combo.setPlaceholderText("Conecta tu Ledger")
        self.dev_combo.currentIndexChanged.connect(self.on_device_selected)
        row.addWidget(self.dev_combo, 1)
        self.btn_refresh = QPushButton("Buscar")
        self.btn_refresh.setObjectName("secondary")
        self.btn_refresh.clicked.connect(self.manual_refresh)
        row.addWidget(self.btn_refresh)
        lay.addLayout(row)
        self.search_status = QLabel("Sin buscar todavía")
        self.search_status.setObjectName("dim")
        lay.addWidget(self.search_status)

        self.model_label = QLabel("Sin dispositivo")
        self.model_label.setObjectName("big")
        lay.addWidget(self.model_label)
        self.fw_value = QLabel("—")
        self.mcu_value = QLabel("—")
        self.status_value = QLabel("—")
        self.secure_value = QLabel("—")
        self.apps_value = QLabel("—")
        self.avail_value = QLabel("—")
        lay.addLayout(kv("Firmware", self.fw_value.text()))
        self.fw_row = lay.itemAt(lay.count() - 1).layout()
        lay.addLayout(kv("MCU", self.mcu_value.text()))
        self.mcu_row = lay.itemAt(lay.count() - 1).layout()
        lay.addLayout(kv("Estado", self.status_value.text()))
        self.status_row = lay.itemAt(lay.count() - 1).layout()
        lay.addLayout(kv("Canal seguro", self.secure_value.text()))
        self.secure_row = lay.itemAt(lay.count() - 1).layout()
        lay.addLayout(kv("Apps instaladas", self.apps_value.text()))
        self.apps_row = lay.itemAt(lay.count() - 1).layout()
        lay.addStretch(1)
        return frame

    def security_card(self):
        frame, lay = card("SEGURIDAD")
        row = QHBoxLayout()
        row.addWidget(QLabel("Genuinidad"))
        row.addStretch(1)
        self.genuine_chip = QLabel("Sin verificar")
        self.genuine_chip.setObjectName("chipMuted")
        row.addWidget(self.genuine_chip)
        lay.addLayout(row)

        line = QHBoxLayout()
        line.setSpacing(8)
        self.btn_genuine = QPushButton("Verificar online")
        self.btn_genuine.setObjectName("secondary")
        self.btn_genuine.clicked.connect(lambda: self.run_genuine(False))
        self.btn_offline = QPushButton("Verificar offline")
        self.btn_offline.setObjectName("secondary")
        self.btn_offline.clicked.connect(lambda: self.run_genuine(True))
        line.addWidget(self.btn_genuine)
        line.addWidget(self.btn_offline)
        lay.addLayout(line)
        note = QLabel("La comprobación offline no envía nada a Ledger. El aviso de permiso solo aparece una vez por arranque; reinicia el Ledger para verlo de nuevo.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.last_check = QLabel("Última verificación: —")
        self.last_check.setObjectName("dim")
        lay.addWidget(self.last_check)
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
        self.update_detail = QLabel("")
        self.update_detail.setObjectName("dim")
        lay.addWidget(self.update_detail)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_update = QPushButton("Actualizar firmware")
        self.btn_update.setObjectName("primary")
        self.btn_update.clicked.connect(self.start_update)
        self.btn_changelog = QPushButton("Novedades…")
        self.btn_changelog.setObjectName("secondary")
        self.btn_changelog.clicked.connect(self.show_changelog)
        buttons.addWidget(self.btn_update)
        buttons.addWidget(self.btn_changelog)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addStretch(1)
        return frame

    def apps_card(self):
        frame, lay = card()
        top = QHBoxLayout()
        h = QLabel("APPS")
        h.setObjectName("h2")
        top.addWidget(h)
        top.addStretch(1)
        self.apps_chip = QLabel("—")
        self.apps_chip.setObjectName("chipMuted")
        top.addWidget(self.apps_chip)
        lay.addLayout(top)

        header = QHBoxLayout()
        for text, stretch, width in (
            ("APP", 1, 0),
            ("INSTALADA", 0, 70),
            ("DISPONIBLE", 0, 80),
            ("ESTADO", 0, 110),
        ):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 10px; color: #6f6f6f; font-weight: 700;")
            if width:
                lbl.setFixedWidth(width)
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            header.addWidget(lbl, stretch)
        lay.addLayout(header)

        self.apps_rows = QVBoxLayout()
        self.apps_rows.setSpacing(7)
        lay.addLayout(self.apps_rows)
        self.btn_apps = QPushButton("Gestionar apps…")
        self.btn_apps.setObjectName("secondary")
        self.btn_apps.clicked.connect(self.open_apps)
        lay.addWidget(self.btn_apps, alignment=Qt.AlignLeft)
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

    def language_card(self):
        frame, lay = card("IDIOMA")
        top = QHBoxLayout()
        self.lang_name = QLabel("—")
        self.lang_name.setStyleSheet("font-weight: 600;")
        top.addWidget(self.lang_name)
        top.addStretch(1)
        self.lang_chip = QLabel("—")
        self.lang_chip.setObjectName("chipMuted")
        top.addWidget(self.lang_chip)
        lay.addLayout(top)
        self.lang_sub = QLabel("Sin dispositivo")
        self.lang_sub.setObjectName("dim")
        lay.addWidget(self.lang_sub)
        self.lang_avail = QLabel("")
        self.lang_avail.setObjectName("dim")
        self.lang_avail.setWordWrap(True)
        lay.addWidget(self.lang_avail)
        self.btn_lang = QPushButton("Cambiar idioma…")
        self.btn_lang.setObjectName("secondary")
        self.btn_lang.clicked.connect(self.open_language)
        lay.addWidget(self.btn_lang, alignment=Qt.AlignLeft)
        lay.addStretch(1)
        return frame

    def preferences_card(self):
        frame, lay = card()
        row = QHBoxLayout()
        title = QLabel("PREFERENCIAS")
        title.setObjectName("h2")
        row.addWidget(title)
        row.addSpacing(18)
        self.pref_restore = QCheckBox("Reinstalar apps después de actualizar")
        self.pref_restore.setChecked(True)
        self.pref_lang = QCheckBox("Conservar pack de idioma")
        self.pref_lang.setChecked(True)
        row.addWidget(self.pref_restore)
        row.addWidget(self.pref_lang)
        row.addStretch(1)
        lay.addLayout(row)
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
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, lambda: self.check_timeout(job))
        job.start()
        return True

    def check_timeout(self, job):
        if self.active_job is job:
            self.active_job = None
            self.append_log("La operación ha tardado demasiado. Se libera la interfaz; si el dispositivo sigue ocupado, desconéctalo y vuelve a conectarlo.", "warn")
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
            InfoDialog(self, "Ledger Updater", error, error=True).exec()

    def set_busy(self, busy, text=None):
        self.busy = busy
        self.apply_button_states()
        if busy:
            self.status_chip.setText(f"● {text or 'Trabajando…'}")
            self.status_chip.setObjectName("chipWarn")
        else:
            self.update_status()
        self.restyle(self.status_chip)

    def restyle(self, w):
        w.style().unpolish(w)
        w.style().polish(w)

    def closeEvent(self, event):
        job = self.active_job
        if job is not None and job.isRunning():
            InfoDialog(
                self,
                "Operación en curso",
                "Hay una operación en curso sobre el dispositivo. "
                "Cerrar la aplicación puede interrumpirla y dejar el dispositivo inservible.",
                error=True,
            ).exec()
            event.ignore()
            return
        super().closeEvent(event)

    def apply_button_states(self):
        busy = self.busy
        online = self.has_device and not self.offline
        self.btn_update.setEnabled(not busy and online and self.can_update)
        self.btn_changelog.setEnabled(not busy and online)
        self.btn_genuine.setEnabled(not busy and online)
        self.btn_offline.setEnabled(not busy and self.has_device)
        self.btn_apps.setEnabled(not busy and online)
        self.btn_lang.setEnabled(not busy and online)
        self.btn_refresh.setEnabled(not busy)
        self.dev_combo.setEnabled(not busy)

    def update_status(self):
        if not self.info:
            text, name = "● Sin dispositivo", "chipMuted"
        elif self.info.get("is_bootloader"):
            text, name = "● Bootloader", "chipWarn"
        elif backend.pin_validated(self.info):
            text, name = "● Conectado", "chipOk"
        else:
            text, name = "● Bloqueado", "chipWarn"
        self.status_chip.setText(text)
        self.status_chip.setObjectName(name)
        self.restyle(self.status_chip)

    def append_log(self, msg, tag=""):
        color = LOG_COLORS.get(tag, LOG_COLORS[""])
        self.logbox.append(f'<span style="color:{color}">{html.escape(str(msg))}</span>')
        self.logbox.verticalScrollBar().setValue(self.logbox.verticalScrollBar().maximum())

    def set_progress(self, cur, total):
        total = max(int(total or 0), 1)
        self.progress.setMaximum(total)
        self.progress.setValue(int(cur))
        self.progress_label.setText(f"{int(cur * 100 / total)} %")

    def show_log(self):
        TextDialog(self, "Registro", self.logbox.toPlainText()).exec()

    # ------------------------------------------------------------------ device
    def refresh_devices(self, announce: bool = False):
        if self.busy:
            if announce:
                self.append_log("Hay una operación en curso; espera a que termine.", "warn")
            return
        try:
            devices = backend.LedgerHID.enumerate()
        except Exception as exc:
            self.search_status.setText("Error al buscar dispositivos")
            self.append_log(f"No se pudo enumerar: {exc}", "error")
            return
        paths = [d["path"] for d in devices]
        labels = [f"{backend.model_for_pid(d.get('product_id', 0)) or 'Ledger'} · {d.get('product_id', 0):04x}" for d in devices]
        if paths != self.dev_paths:
            self.dev_paths = paths
            self.devices = devices
            current = self.path
            if paths:
                self.append_log(f"Detectado: {labels[0]}", "ok")
            else:
                self.append_log("No se encontró ningún Ledger conectado.", "warn")
            self.dev_combo.blockSignals(True)
            self.dev_combo.clear()
            self.dev_combo.addItems(labels)
            if current in paths:
                self.dev_combo.setCurrentIndex(paths.index(current))
            self.dev_combo.blockSignals(False)
            if not paths:
                self.path = None
                self.info = None
                self.clear_device()
            elif current not in paths:
                self.path = paths[0]
                self.dev_combo.setCurrentIndex(0)
                self.load_device()
        count = len(paths)
        label = "1 dispositivo" if count == 1 else f"{count} dispositivos"
        self.search_status.setText(f"Última búsqueda: {time.strftime('%H:%M:%S')} · {label}")
        if announce:
            self.append_log(f"Búsqueda: {label}." if count else "Búsqueda: no hay ningún Ledger conectado.", "dim" if count else "warn")

    def manual_refresh(self):
        self.append_log("Buscando dispositivos Ledger…", "dim")
        self.refresh_devices(announce=True)
        if self.path and not self.busy and self.info is None:
            self.load_device()

    def watch_devices(self):
        if not self.busy:
            self.refresh_devices(False)

    def on_device_selected(self, index):
        if 0 <= index < len(self.dev_paths):
            self.path = self.dev_paths[index]
            self.info = None
            self.load_device()

    def clear_device(self):
        self.has_device = False
        self.can_update = False
        self.installed = []
        self.langs = []
        self.catalog = []
        self.meta = []
        self.plan = None
        self.update_names = []
        self.model_label.setText("Sin dispositivo")
        self.device_chip.setText("—")
        self.status_chip.setText("● Sin dispositivo")
        self.status_chip.setObjectName("chipMuted")
        self.restyle(self.status_chip)
        self.update_rows("—", "—", "—", "—", "—")
        self.fw_chip.setText("Sin datos")
        self.fw_chip.setObjectName("chipMuted")
        self.restyle(self.fw_chip)
        self.current_label.setText("—")
        self.target_label.setText("—")
        self.notes_label.setText("Conecta el dispositivo para consultar la versión.")
        self.update_detail.setText("")
        self.apps_chip.setText("—")
        self.apps_chip.setObjectName("chipMuted")
        self.restyle(self.apps_chip)
        self.clear_layout(self.apps_rows)
        empty = QLabel("Conecta el dispositivo para ver apps")
        empty.setObjectName("dim")
        self.apps_rows.addWidget(empty)
        self.lang_name.setText("—")
        self.lang_chip.setText("—")
        self.lang_chip.setObjectName("chipMuted")
        self.restyle(self.lang_chip)
        self.lang_sub.setText("Sin dispositivo")
        self.lang_avail.setText("")
        self.genuine_chip.setText("Sin verificar")
        self.genuine_chip.setObjectName("chipMuted")
        self.restyle(self.genuine_chip)
        self.last_check.setText("Última verificación: —")
        self.device_chip.setText("")
        self.apply_button_states()

    def set_row(self, row, value):
        if row is not None and row.count() >= 3:
            row.itemAt(2).widget().setText(value)

    def update_rows(self, fw, mcu, status, secure, apps):
        for row, value in (
            (self.fw_row, fw), (self.mcu_row, mcu), (self.status_row, status),
            (self.secure_row, secure), (self.apps_row, apps),
        ):
            self.set_row(row, value)

    def load_device(self):
        if not self.path or self.busy:
            return

        def fn(log, progress):
            dev, info = backend.open_path(self.path, log)
            try:
                backend.quit_app(dev)
                time.sleep(0.6)
                info = backend.get_firmware_info(dev)
                try:
                    apps = backend.list_installed_apps(dev)
                except Exception as exc:
                    log(f"No se pudieron leer las apps: {exc}", "warn")
                    apps = []
                try:
                    langs = backend.list_language_packs(dev)
                except Exception as exc:
                    log(f"No se pudo leer el idioma: {exc}", "warn")
                    langs = []
            finally:
                dev.close()
            return info, apps, langs

        self.submit(
            fn,
            on_result=self.apply_device,
            on_error=lambda e: self.append_log(f"Dispositivo: {e}", "warn"),
            busy_text="Leyendo dispositivo…",
            timeout_ms=90000,
        )

    def apply_device(self, result):
        info, apps, langs = result
        self.info, self.installed, self.langs = info, apps, langs
        self.has_device = True
        target = info.get("se_target_id") or info.get("target_id")
        model = backend.model_for_target(target) or "Ledger"
        self.model_label.setText(model)
        fw_short = backend.clean_version(info.get("se_version") or "")
        self.device_chip.setText(f"{model.replace('Ledger ', '')} · {fw_short}")
        idx = self.dev_combo.currentIndex()
        if idx >= 0:
            self.dev_combo.setItemText(idx, f"{model} · {fw_short}")
        fw = backend.clean_version(info.get("se_version") or "")
        mcu = info.get("mcu_version") or "—"
        if info.get("is_bootloader"):
            status = "Bootloader"
        elif backend.pin_validated(info):
            status = "Desbloqueado"
        else:
            status = "Bloqueado"
        secure = "Permitido" if backend.manager_allowed(info) else "No permitido"
        self.update_rows(fw or "—", mcu, status, secure, str(len(apps)))
        self.status_chip.setText("● Conectado")
        self.status_chip.setObjectName("chipOk")
        self.restyle(self.status_chip)
        self.fill_apps_preview()
        self.fill_language()
        self.apply_button_states()
        self.load_plan()

    def load_plan(self):
        if self.offline or not self.path:
            return
        installed = list(self.installed)

        def fn(log, progress):
            plan = backend.update_plan(self.path, log)
            if not plan or not plan.get("latest"):
                return plan, [], []
            catalog = backend.app_catalog(plan["target_id"], plan["latest"]["version"], plan["provider"])
            meta = backend.apps_by_hash([a.get("hash", "") for a in installed])
            return plan, catalog, meta

        self.submit(
            fn,
            on_result=self.apply_plan,
            on_error=lambda e: self.append_log(f"No se pudo consultar el firmware: {e}", "warn"),
            busy_text="Consultando firmware…",
            timeout_ms=90000,
        )

    def apply_plan(self, result):
        plan, catalog, meta = result
        self.plan, self.catalog, self.meta = plan, catalog, meta
        if not plan or not plan.get("latest"):
            self.target_label.setText("—")
            self.notes_label.setText("No hay firmware publicado para este modelo.")
            self.fw_chip.setText("Sin datos")
            self.fw_chip.setObjectName("chipMuted")
            self.restyle(self.fw_chip)
            return
        latest = plan["latest"]
        current = plan["current"] or "—"
        self.current_label.setText(current)
        self.target_label.setText(latest["version"])
        self.set_row(self.fw_row, current)
        notes = backend.notes_for_final(latest)
        first = notes.splitlines()[0] if notes else ""
        self.notes_label.setText(first or "Sin notas publicadas para esta versión.")
        if current == latest["version"]:
            self.fw_chip.setText("Actualizado")
            self.fw_chip.setObjectName("chipOk")
            self.btn_update.setText("Última versión")
            self.can_update = False
            self.update_detail.setText("Ya tienes la última versión.")
        else:
            self.fw_chip.setText("Actualización disponible")
            self.fw_chip.setObjectName("chipWarn")
            self.btn_update.setText("Actualizar firmware")
            self.can_update = True
            self.update_detail.setText(f"Ruta: {current} → {latest['version']}")
        self.restyle(self.fw_chip)
        self.fill_apps_preview()
        self.apply_button_states()

    # ------------------------------------------------------------------ apps preview
    def fill_apps_preview(self):
        self.clear_layout(self.apps_rows)
        installed_by = {a["name"].strip().lower(): a for a in self.installed}
        catalog_by = {a["versionName"].strip().lower(): a for a in self.catalog} if self.catalog else {}
        meta_by = {}
        for a, m in zip(self.installed, self.meta, strict=False):
            if m:
                meta_by[a.get("hash", "")] = m
        names = list(installed_by.keys())[:6]
        self.update_names = []
        for key in names:
            app = catalog_by.get(key)
            inst = installed_by[key]
            inst_ver = (meta_by.get(inst.get("hash", "")) or {}).get("version") or "—"
            avail = app.get("version", "—") if app else "—"
            state, color = "Instalada", GREEN
            if app and inst.get("hash") != app.get("hash"):
                state, color = "Actualizable", ORANGE
                self.update_names.append(app.get("versionName"))
            row = QHBoxLayout()
            n = QLabel(inst["name"])
            n.setStyleSheet("font-weight: 600;")
            row.addWidget(n, 1)
            for text, width in ((inst_ver, 70), (avail, 80)):
                lbl = QLabel(text)
                lbl.setObjectName("dim")
                lbl.setFixedWidth(width)
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                row.addWidget(lbl)
            st = QLabel(state)
            st.setStyleSheet(f"color: {color}; font-weight: 600;")
            st.setFixedWidth(110)
            st.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(st)
            self.apps_rows.addLayout(row)
        if not names:
            empty = QLabel("Sin apps instaladas")
            empty.setObjectName("dim")
            self.apps_rows.addWidget(empty)
        count = len(self.update_names)
        self.apps_chip.setText(f"{count} actualizables" if count else "Al día")
        self.apps_chip.setObjectName("chipWarn" if count else "chipOk")
        self.restyle(self.apps_chip)

    def fill_language(self):
        if not self.langs:
            self.lang_name.setText("English")
            self.lang_chip.setText("Base")
            self.lang_chip.setObjectName("chipMuted")
            self.lang_sub.setText("Sin pack adicional instalado")
        else:
            pack = self.langs[0]
            name = backend.LANGUAGE_NAMES.get(pack["id"], str(pack["id"])).capitalize()
            self.lang_name.setText(name)
            self.lang_chip.setText("Instalado")
            self.lang_chip.setObjectName("chipOk")
            self.lang_sub.setText(f"Pack id {pack['id']} · {pack.get('size', '?')} bytes")
        self.lang_avail.setText("Disponibles: English · French · Spanish · German · Russian · Turkish")
        self.restyle(self.lang_chip)

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                self.clear_layout(item.layout())

    # ------------------------------------------------------------------ actions
    def toggle_offline(self, checked):
        self.offline = bool(checked)
        backend.set_offline(self.offline)
        self.append_log("Modo offline activado: sin red." if checked else "Modo online activado.", "warn" if checked else "dim")
        self.apply_button_states()

    def run_genuine(self, offline: bool):
        if not self.path or self.busy:
            return
        if not offline:
            def fn(log, progress):
                if not backend.offline_genuine_check(self.path, log):
                    return False
                return backend.genuine_check(self.path, log, progress)
        else:
            def fn(log, progress):
                return backend.offline_genuine_check(self.path, log)
        self.submit(fn, on_result=lambda ok: self.set_genuine(ok, offline), busy_text="Verificando…", timeout_ms=0)

    def set_genuine(self, ok, offline):
        self.genuine_chip.setText("● Genuino" if ok else "No verificado")
        self.genuine_chip.setObjectName("chipOk" if ok else "chipBad")
        self.last_check.setText(f"Última verificación: {'offline' if offline else 'online'} · ahora")
        self.restyle(self.genuine_chip)

    def show_changelog(self):
        if self.busy or self.offline:
            return

        def fn(log, progress):
            plan = backend.update_plan(self.path, log)
            return backend.changelog_text(plan)

        self.submit(fn, on_result=lambda text: TextDialog(self, "Novedades", text).exec(), busy_text="Cargando novedades…", timeout_ms=90000)

    def start_update(self):
        if self.busy or self.offline:
            return
        if not self.plan or not self.plan.get("latest"):
            InfoDialog(self, "Actualizar", "No hay información de firmware disponible.").exec()
            return
        latest = self.plan["latest"]["version"]
        current = self.plan["current"] or "—"
        if not ConfirmDialog(
            self,
            "Actualizar firmware",
            f"Se va a actualizar {current} → {latest}.\n\n"
            "· No desconectes el dispositivo.\n"
            "· Confirma en la pantalla del Ledger.\n"
            "· Se guardará la lista de apps y el idioma.",
            confirm="Actualizar",
            danger=True,
        ).exec():
            return
        restore = self.pref_restore.isChecked()
        restore_lang = self.pref_lang.isChecked()

        def fn(log, progress):
            return backend.update_device(
                self.path, log, progress, restore=restore, restore_language=restore_lang
            )

        self.set_progress(0, 100)
        self.submit(fn, on_result=self.update_done, busy_text="Actualizando firmware…", timeout_ms=0)

    def update_done(self, info):
        self.append_log("Actualización completada.", "ok")
        self.progress.setValue(100)
        self.progress_label.setText("100 %")
        QTimer.singleShot(1200, self.load_device)

    def open_apps(self):
        if self.busy or self.offline:
            return
        AppsDialog(self).exec()

    def open_language(self):
        if self.busy or self.offline:
            return
        LanguageDialog(self).exec()


class AppsDialog(QDialog):
    def __init__(self, main: MainWindow):
        super().__init__(main)
        self.main = main
        self.catalog = []
        self.installed = []
        self.meta = []
        self.version = ""
        self.setWindowTitle("Gestionar apps · Ledger")
        self.setMinimumSize(920, 700)
        self.setStyleSheet(QSS)
        self.setWindowIcon(QIcon(str(ICON)))
        self.build()
        self.load()

    def build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        h = QLabel("Gestionar apps")
        h.setObjectName("h1")
        self.sub = QLabel("Leyendo dispositivo…")
        self.sub.setObjectName("dim")
        titles.addWidget(h)
        titles.addWidget(self.sub)
        top.addLayout(titles)
        top.addStretch(1)
        self.chip_cat = QLabel("—")
        self.chip_cat.setObjectName("chipMuted")
        self.chip_upd = QLabel("—")
        self.chip_upd.setObjectName("chipMuted")
        top.addWidget(self.chip_cat)
        top.addWidget(self.chip_upd)
        lay.addLayout(top)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar app…")
        self.search.textChanged.connect(self.filter_rows)
        lay.addWidget(self.search)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["APP", "INSTALADA", "DISPONIBLE", "ESTADO", ""])
        self.table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 100), (2, 100), (3, 130), (4, 130)):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, width)
        self.table.verticalHeader().setDefaultSectionSize(46)
        lay.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_all = QPushButton("Actualizar todas")
        self.btn_all.setObjectName("primary")
        self.btn_all.clicked.connect(self.update_all)
        self.btn_export = QPushButton("Exportar lista…")
        self.btn_export.setObjectName("secondary")
        self.btn_export.clicked.connect(self.export)
        close = QPushButton("Cerrar")
        close.setObjectName("secondary")
        close.clicked.connect(self.accept)
        bottom.addWidget(self.btn_all)
        bottom.addWidget(self.btn_export)
        bottom.addStretch(1)
        bottom.addWidget(close)
        lay.addLayout(bottom)

    def load(self):
        def fn(log, progress):
            dev, info = backend.open_path(self.main.path, log)
            try:
                backend.quit_app(dev)
                time.sleep(0.6)
                info = backend.get_firmware_info(dev)
                target = info.get("se_target_id") or info.get("target_id")
                version = backend.clean_version(info.get("se_version") or "")
                provider = backend.provider_from_version(info.get("se_version") or "")
                catalog = backend.app_catalog(target, version, provider)
                installed = backend.list_installed_apps(dev)
                meta = backend.apps_by_hash([a.get("hash", "") for a in installed])
            finally:
                dev.close()
            return catalog, installed, meta, version

        self.main.submit(fn, on_result=self.fill, busy_text="Leyendo apps…", timeout_ms=90000)

    def fill(self, result):
        self.catalog, self.installed, self.meta, self.version = result
        self.sub.setText(f"Firmware {self.version} · catálogo de Ledger")
        self.chip_cat.setText(f"{len(self.catalog)} en catálogo")
        self.chip_cat.setObjectName("chipMuted")
        self.main.restyle(self.chip_cat)
        self.render()

    def state_of(self, app, inst):
        if not inst:
            return "", DIM
        key = (app.get("versionName") or "").strip().lower()
        if key in backend.DYNAMIC_HASHES:
            return "Instalada", GREEN
        if inst.get("hash") == app.get("hash"):
            return "Actualizada", GREEN
        meta = next((m for a, m in zip(self.installed, self.meta, strict=False) if a is inst and m), None)
        inst_ver = (meta or {}).get("version") or ""
        avail = app.get("version") or ""
        if inst_ver and avail and backend.semver_gt(avail, inst_ver):
            return "Actualizable", ORANGE
        return "Actualizable", ORANGE

    def render(self):
        installed_by = {a["name"].strip().lower(): a for a in self.installed}
        self.table.setRowCount(0)
        updates = 0
        rows = []
        for app in self.catalog:
            key = (app.get("versionName") or "").strip().lower()
            inst = installed_by.get(key)
            state, color = self.state_of(app, inst)
            if state == "Actualizable":
                updates += 1
            rows.append((app, inst, state, color))
        for a in self.installed:
            key = a["name"].strip().lower()
            if key not in {(x.get("versionName") or "").strip().lower() for x in self.catalog}:
                rows.append((None, a, "Instalada", GREEN))
        self.table.setRowCount(len(rows))
        for r, (app, inst, state, color) in enumerate(rows):
            name = (app or {}).get("versionName") or inst["name"]
            meta = next((m for a, m in zip(self.installed, self.meta, strict=False) if a is inst and m), None)
            inst_ver = (meta or {}).get("version") or (inst or {}).get("version") or "—" if inst else "—"
            avail = app.get("version") if app else "—"
            self.table.setItem(r, 0, QTableWidgetItem(name))
            for col, text in ((1, inst_ver), (2, avail or "—")):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, col, item)
            st = QTableWidgetItem(state or "")
            st.setForeground(QColor(color))
            st.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 3, st)
            if app:
                label = "Actualizar" if state == "Actualizable" else ("Instalar" if not inst else "Desinstalar")
                btn = QPushButton(label)
                btn.setObjectName("primary" if state == "Actualizable" else "secondary")
                btn.setFixedHeight(30)
                if label == "Desinstalar":
                    btn.clicked.connect(lambda _=False, n=name: self.uninstall(n))
                else:
                    btn.clicked.connect(lambda _=False, n=name: self.install(n))
                wrap = QWidget()
                wl = QHBoxLayout(wrap)
                wl.setContentsMargins(0, 0, 6, 0)
                wl.addWidget(btn)
                self.table.setCellWidget(r, 4, wrap)
        self.chip_upd.setText(f"{updates} actualizables" if updates else "Al día")
        self.chip_upd.setObjectName("chipWarn" if updates else "chipOk")
        self.main.restyle(self.chip_upd)
        self.btn_all.setEnabled(updates > 0)

    def filter_rows(self, text):
        text = text.strip().lower()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            self.table.setRowHidden(row, bool(text) and text not in (item.text().lower() if item else ""))

    def install(self, name):
        def fn(log, progress):
            dev, info = backend.open_path(self.main.path, log)
            try:
                backend.quit_app(dev)
                time.sleep(0.6)
                info = backend.get_firmware_info(dev)
                target = info.get("se_target_id") or info.get("target_id")
                installed = [a["name"] for a in backend.list_installed_apps(dev)]
                backend.install_app_entry(dev, target, self.catalog, name, installed, log, progress)
            finally:
                dev.close()
            return name

        self.main.submit(fn, on_result=lambda _: self.load(), busy_text=f"Instalando {name}…", timeout_ms=0)

    def uninstall(self, name):
        app = next((a for a in self.catalog if (a.get("versionName") or "").strip().lower() == name.strip().lower()), None)
        if not app:
            return

        def fn(log, progress):
            dev, info = backend.open_path(self.main.path, log)
            try:
                backend.quit_app(dev)
                time.sleep(0.6)
                info = backend.get_firmware_info(dev)
                target = info.get("se_target_id") or info.get("target_id")
                backend.uninstall_app_hsm(dev, target, app, log, progress)
            finally:
                dev.close()
            return name

        self.main.submit(fn, on_result=lambda _: self.load(), busy_text=f"Desinstalando {name}…", timeout_ms=0)

    def update_all(self):
        names = []
        installed_by = {a["name"].strip().lower(): a for a in self.installed}
        for app in self.catalog:
            key = (app.get("versionName") or "").strip().lower()
            if self.state_of(app, installed_by.get(key))[0] == "Actualizable":
                names.append(app["versionName"])
        if not names:
            return
        if not ConfirmDialog(
            self,
            "Actualizar apps",
            "Se actualizarán:\n" + "\n".join(f"· {n}" for n in names),
            confirm="Actualizar",
        ).exec():
            return

        def fn(log, progress):
            for name in names:
                dev, info = backend.open_path(self.main.path, log)
                try:
                    backend.quit_app(dev)
                    time.sleep(0.6)
                    info = backend.get_firmware_info(dev)
                    target = info.get("se_target_id") or info.get("target_id")
                    installed = [a["name"] for a in backend.list_installed_apps(dev)]
                    backend.install_app_entry(dev, target, self.catalog, name, installed, log, progress)
                finally:
                    dev.close()
            return names

        self.main.submit(fn, on_result=lambda _: self.load(), busy_text="Actualizando apps…", timeout_ms=0)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar lista de apps", "ledger-apps.json", "JSON (*.json)")
        if not path:
            return
        apps = []
        for a in self.installed:
            meta = next((m for x, m in zip(self.installed, self.meta, strict=False) if x is a and m), None)
            apps.append(
                {
                    "name": a.get("name"),
                    "blocks": a.get("blocks"),
                    "flags": a.get("flags"),
                    "hash": a.get("hash"),
                    "version": (meta or {}).get("version"),
                }
            )
        payload = {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "firmware": self.version,
            "apps": apps,
        }
        try:
            Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self.main.append_log(f"Lista exportada a {path}", "ok")
        except Exception as exc:
            InfoDialog(self, "Exportar", str(exc), error=True).exec()


class LanguageDialog(QDialog):
    def __init__(self, main: MainWindow):
        super().__init__(main)
        self.main = main
        self.packs = []
        self.current = []
        self.dv_id = None
        self.final_id = None
        self.setWindowTitle("Idioma · Ledger")
        self.setMinimumSize(520, 420)
        self.setStyleSheet(QSS)
        self.setWindowIcon(QIcon(str(ICON)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)
        h = QLabel("Pack de idioma")
        h.setObjectName("h1")
        lay.addWidget(h)
        self.current_label = QLabel("Leyendo dispositivo…")
        self.current_label.setObjectName("dim")
        lay.addWidget(self.current_label)
        self.combo = QComboBox()
        lay.addWidget(self.combo)
        row = QHBoxLayout()
        self.btn = QPushButton("Instalar idioma")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self.apply)
        close = QPushButton("Cerrar")
        close.setObjectName("secondary")
        close.clicked.connect(self.accept)
        row.addWidget(self.btn)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        lay.addWidget(self.status)
        lay.addStretch(1)
        self.load()

    def load(self):
        def fn(log, progress):
            dev, info = backend.open_path(self.main.path, log)
            try:
                backend.quit_app(dev)
                time.sleep(0.6)
                info = backend.get_firmware_info(dev)
                target = info.get("se_target_id") or info.get("target_id")
                version = backend.clean_version(info.get("se_version") or "")
                provider = backend.provider_from_version(info.get("se_version") or "")
                dv = backend.api_get("/get_device_version", provider=provider, target_id=target)
                try:
                    cur = backend.api_get("/get_firmware_version", device_version=dv["id"], version_name=version, provider=provider)
                    final_id = cur["id"]
                except Exception:
                    latest = backend.latest_final(backend.fetch_all_finals(), dv["id"])
                    final_id = latest["id"] if latest else None
                packs = backend.language_catalog(dv["id"], final_id) if final_id else []
                current = backend.list_language_packs(dev)
            finally:
                dev.close()
            return packs, current, dv["id"], final_id

        self.main.submit(fn, on_result=self.fill, busy_text="Leyendo idiomas…", timeout_ms=90000)

    def fill(self, result):
        self.packs, self.current, self.dv_id, self.final_id = result
        self.combo.clear()
        for p in self.packs:
            self.combo.addItem(f"{(p.get('language') or '').capitalize()}  ·  {p.get('version', '')}")
        ids = [p["id"] for p in self.current]
        if ids:
            self.current_label.setText(f"Idioma actual: {backend.LANGUAGE_NAMES.get(ids[0], ids[0]).capitalize()}")
        else:
            self.current_label.setText("Idioma actual: English (base)")
        for i, p in enumerate(self.packs):
            if backend.LANGUAGE_NAME_TO_ID.get((p.get("language") or "").lower()) in ids:
                self.combo.setCurrentIndex(i)
                break

    def apply(self):
        idx = self.combo.currentIndex()
        if idx < 0 or self.main.busy:
            return
        pack = self.packs[idx]
        lang_id = backend.LANGUAGE_NAME_TO_ID.get((pack.get("language") or "").lower())
        if lang_id is None:
            return
        name = (pack.get("language") or "").capitalize()

        def fn(log, progress):
            dev, _info = backend.open_path(self.main.path, log)
            try:
                backend.quit_app(dev)
                time.sleep(0.6)
                backend.install_language_pack(dev, self.dv_id, self.final_id, lang_id, log, progress)
            finally:
                dev.close()
            return lang_id

        self.main.submit(fn, on_result=lambda _: self.load(), busy_text=f"Instalando {name}…", timeout_ms=0)
        self.status.setText(f"Instalando {name}…")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Actualizador de firmware (Ledger)")
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    font_path = _res("common", "fonts", "InterVariable.ttf")
    if font_path.exists():
        fid = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            app.setFont(QFont(families[0], 10))
    win = MainWindow()
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
