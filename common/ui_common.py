"""Componentes visuales compartidos para los updaters en PySide6."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

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
QPushButton#danger {{
    background: #a03030; color: #f5d7d7; border: 1px solid #7a2222;
    border-radius: 10px; padding: 10px 18px; font-weight: 700;
}}
QPushButton#danger:hover {{ background: #c0392b; }}
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
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333333; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def install_font(app, path: Path):
    if path.exists():
        fid = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            app.setFont(QFont(families[0], 10))


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


class BaseDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 560):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(QSS)
        self.setModal(True)
        self.setMinimumWidth(width)
        if parent is not None:
            icon = parent.windowIcon()
            if icon is not None and not icon.isNull():
                self.setWindowIcon(icon)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 22, 24, 20)
        self.lay.setSpacing(14)
        h = QLabel(title)
        h.setObjectName("h1")
        self.lay.addWidget(h)

    def add_body(self, text: str):
        body = QLabel(text)
        body.setObjectName("dim")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lay.addWidget(body)

    def add_buttons(self, confirm: str, cancel: str | None = "Cancelar", danger: bool = False):
        row = QHBoxLayout()
        row.addStretch(1)
        if cancel:
            b = QPushButton(cancel)
            b.setObjectName("secondary")
            b.clicked.connect(self.reject)
            row.addWidget(b)
        ok = QPushButton(confirm)
        ok.setObjectName("danger" if danger else "primary")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        row.addWidget(ok)
        self.lay.addLayout(row)


class TextDialog(BaseDialog):
    def __init__(self, parent, title: str, body: str):
        super().__init__(parent, title, width=720)
        self.resize(780, 540)
        text = QTextEdit()
        text.setObjectName("log")
        text.setReadOnly(True)
        text.setPlainText(body)
        self.lay.addWidget(text, 1)
        self.add_buttons("Cerrar", cancel=None)


class InfoDialog(BaseDialog):
    """Aviso de una sola acción, con el mismo estilo que el resto de diálogos."""

    def __init__(self, parent, title: str, message: str, confirm: str = "Aceptar", error: bool = False):
        super().__init__(parent, title)
        self.add_body(message)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton(confirm)
        ok.setObjectName("danger" if error else "primary")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        row.addWidget(ok)
        self.lay.addLayout(row)

    def exec(self):
        super().exec()
        return True


class ConfirmDialog(BaseDialog):
    def __init__(self, parent, title: str, message: str, confirm: str = "Continuar", cancel: str = "Cancelar", danger: bool = False):
        super().__init__(parent, title)
        self.add_body(message)
        self.add_buttons(confirm, cancel, danger)

    def exec(self):
        return super().exec() == QDialog.Accepted


class ChoiceDialog(BaseDialog):
    def __init__(self, parent, title: str, label: str, options, current: int = 0):
        super().__init__(parent, title)
        lbl = QLabel(label)
        lbl.setObjectName("dim")
        self.lay.addWidget(lbl)
        self.combo = QComboBox()
        self.combo.addItems(list(options))
        if 0 <= current < self.combo.count():
            self.combo.setCurrentIndex(current)
        self.lay.addWidget(self.combo)
        self.add_buttons("Continuar", "Cancelar")

    def exec(self):
        return super().exec() == QDialog.Accepted

    def selected(self):
        return self.combo.currentIndex()


class InputDialog(BaseDialog):
    def __init__(self, parent, title: str, label: str, initial: str = "", password: bool = False):
        super().__init__(parent, title)
        lbl = QLabel(label)
        lbl.setObjectName("dim")
        lbl.setWordWrap(True)
        self.lay.addWidget(lbl)
        self.edit = QLineEdit(initial)
        if password:
            self.edit.setEchoMode(QLineEdit.Password)
        self.lay.addWidget(self.edit)
        self.add_buttons("Aceptar", "Cancelar")

    def exec(self):
        return super().exec() == QDialog.Accepted

    def value(self):
        return self.edit.text()
