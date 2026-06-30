"""Entry point: python -m galvo_gui"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from galvo_gui.gui.main_window import MainWindow
from galvo_gui.gui.theme import apply_theme

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ICON_PATH = _REPO_ROOT / "icon.ico"
_WINDOWS_APP_ID = "galvo_gui.app"


def _load_app_icon() -> QIcon:
    return QIcon(str(_ICON_PATH))


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_WINDOWS_APP_ID)
    except (AttributeError, OSError):
        pass


def _configure_application(app: QApplication) -> None:
    _set_windows_app_id()
    icon = _load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)


def main() -> None:
    app = QApplication(sys.argv)
    _configure_application(app)
    app.setApplicationName("galvo-gui")
    app.setOrganizationName("galvo_gui")
    apply_theme(app)

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    window.resize(1100, 720)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
