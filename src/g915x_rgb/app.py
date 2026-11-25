"""Entry point for the Kirigami interface."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from .editor import Editor
from .logging_setup import configure as configure_logging

APP_ID = "io.github.joshman1019.g915x_rgb"


def build(app: QApplication) -> tuple[QQmlApplicationEngine, Editor]:
    editor = Editor(app)
    engine = QQmlApplicationEngine()
    engine.setInitialProperties({"editor": editor})
    engine.load(str(Path(__file__).resolve().parent / "qml" / "Main.qml"))
    return engine, editor


def main() -> int:
    configure_logging("gui")
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "org.kde.desktop")
    app = QApplication(sys.argv)
    app.setApplicationName("g915x-rgb")
    app.setApplicationDisplayName("G915 X Lighting")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon.fromTheme("input-keyboard"))
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    ticker = QTimer(interval=500)       # lets Python see Ctrl+C
    ticker.timeout.connect(lambda: None)
    ticker.start()
    engine, editor = build(app)
    if not engine.rootObjects():
        return 1
    app.aboutToQuit.connect(editor.shutdown)
    code = app.exec()
    del engine                          # before the objects QML refers to
    return code


if __name__ == "__main__":
    sys.exit(main())
