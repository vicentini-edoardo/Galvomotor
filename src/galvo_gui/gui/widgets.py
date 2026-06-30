"""Small reusable GUI widgets."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QTextEdit, QWidget

from galvo_gui.gui import theme


class StatusLed(QLabel):
    """Colored circular status indicator."""

    _COLOR_MAP = {
        "gray": theme.BORDER,
        "orange": theme.ACCENT_WARN,
        "green": theme.ACCENT_OK,
        "red": theme.ACCENT_ERR,
    }

    def __init__(self, label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setFixedSize(14, 14)
        self.set_state("gray")

    def set_state(self, color: str) -> None:
        hex_color = self._COLOR_MAP.get(color, color)
        self.setStyleSheet(
            f"border-radius: 7px; background: {hex_color}; border: 1px solid rgba(0,0,0,60);"
        )


class LogView(QTextEdit):
    """Read-only status log."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(80)

    def append_line(self, message: str) -> None:
        self.append(message)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class ReadoutLabel(QLabel):
    """Prominent numeric readout label."""

    def __init__(self, text: str = "--", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("ReadoutLabel")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
