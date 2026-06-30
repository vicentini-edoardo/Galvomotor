"""Entry point: python -m galvo_gui"""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from galvo_gui.gui.main_window import MainWindow
from galvo_gui.gui.theme import apply_theme


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("galvo-gui")
    app.setOrganizationName("galvo_gui")
    apply_theme(app)

    window = MainWindow()
    window.resize(1100, 720)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
