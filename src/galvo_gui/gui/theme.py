"""Dark theme: palette, global QSS, pyqtgraph config, and plot helpers.

Copied from Andor_idus420_Demodulation_gui with package name unchanged.
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BG = "#0e1116"
SURFACE = "#161b22"
SURFACE_ALT = "#1c232c"
BORDER = "#2a313c"
TEXT = "#e6edf3"
TEXT_MUTED = "#8b949e"
ACCENT = "#4cc2ff"
ACCENT_WARN = "#f0b429"
ACCENT_OK = "#3fb950"
ACCENT_ERR = "#f85149"

CURVE_YELLOW = "#ffd166"
CURVE_CYAN = "#5ad1ff"
CURVE_MAGENTA = "#ff7ad9"
CURVE_GREEN = "#6fe39f"

# ---------------------------------------------------------------------------
# Global QSS
# ---------------------------------------------------------------------------

STYLESHEET = f"""
/* Base */
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-size: 10pt;
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

/* Tab bar */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background-color: {SURFACE};
    border-radius: 4px;
}}
QTabBar::tab {{
    background-color: {SURFACE_ALT};
    color: {TEXT_MUTED};
    padding: 6px 18px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    border: 1px solid {BORDER};
    border-bottom: none;
}}
QTabBar::tab:selected {{
    background-color: {SURFACE};
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover:!selected {{
    background-color: {SURFACE};
    color: {TEXT};
}}
QTabBar::tab:disabled {{
    color: {BORDER};
}}

/* Group boxes */
QGroupBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 20px;
    padding: 8px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 2px;
    color: {TEXT_MUTED};
    font-size: 9pt;
    letter-spacing: 0.5px;
}}

/* Labels */
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLabel#ReadoutLabel {{
    font-size: 14pt;
    font-weight: 700;
    padding: 8px 12px;
    background: {SURFACE_ALT};
    color: {TEXT};
    border-radius: 6px;
    border: 1px solid {BORDER};
}}
QStatusBar QLabel {{
    color: {TEXT_MUTED};
    font-size: 9pt;
    padding: 0 6px;
}}
QLabel#connection_label[connected="true"] {{
    color: {ACCENT_OK};
    font-weight: 600;
}}
QLabel#connection_label[connected="false"] {{
    color: {ACCENT_ERR};
}}
QLabel#acquisition_label[running="true"] {{
    color: {ACCENT_WARN};
    font-weight: 600;
}}

/* Buttons */
QPushButton {{
    background-color: {SURFACE_ALT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 12px;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: #253040;
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: #1a2530;
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {SURFACE};
    border-color: {BORDER};
}}
QPushButton[pending="true"] {{
    color: {ACCENT_WARN};
    font-weight: 600;
    border-color: {ACCENT_WARN};
}}
QPushButton[accent="true"] {{
    background-color: #1a3a50;
    color: {ACCENT};
    border-color: {ACCENT};
    font-weight: 600;
}}

/* Input widgets */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background-color: {SURFACE_ALT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 6px;
    min-height: 22px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_MUTED};
    width: 0;
    height: 0;
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_ALT};
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: {BG};
    border: 1px solid {BORDER};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {BORDER};
    border: none;
    width: 14px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {ACCENT};
}}

/* Checkboxes */
QCheckBox {{
    color: {TEXT};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background-color: {SURFACE_ALT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}

/* Progress bar */
QProgressBar {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    text-align: center;
    color: {TEXT};
    height: 14px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}

/* Status bar */
QStatusBar {{
    background-color: {SURFACE_ALT};
    border-top: 1px solid {BORDER};
    color: {TEXT_MUTED};
}}

/* Splitter */
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 4px;
}}
QSplitter::handle:hover {{
    background-color: {ACCENT};
}}

/* Scrollbars */
QScrollBar:vertical {{
    background: {SURFACE};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {SURFACE};
    height: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {TEXT_MUTED};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* Text edit (LogView) */
QTextEdit {{
    background-color: {SURFACE_ALT};
    color: {TEXT_MUTED};
    border: 1px solid {BORDER};
    border-radius: 4px;
    font-size: 9pt;
}}
"""


def apply_theme(app: QApplication) -> None:
    """Apply dark palette, global QSS, and pyqtgraph defaults."""
    palette = QPalette()
    bg = QColor(BG)
    surface = QColor(SURFACE)
    text = QColor(TEXT)
    text_muted = QColor(TEXT_MUTED)
    accent = QColor(ACCENT)
    border = QColor(BORDER)

    palette.setColor(QPalette.ColorRole.Window, bg)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, surface)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.BrightText, text)
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE_ALT))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, bg)
    palette.setColor(QPalette.ColorRole.PlaceholderText, text_muted)
    palette.setColor(QPalette.ColorRole.Mid, border)
    palette.setColor(QPalette.ColorRole.Dark, border)
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Link, accent)

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, text_muted)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, text_muted)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, text_muted)

    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

    font = QFont()
    font.setPointSize(10)
    app.setFont(font)

    pg.setConfigOptions(
        background=BG,
        foreground=TEXT,
        antialias=True,
    )


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def _style_plot_item(plot_item: pg.PlotItem) -> None:
    for axis_name in ("bottom", "left", "top", "right"):
        ax = plot_item.getAxis(axis_name)
        ax.setPen(pg.mkPen(BORDER, width=1))
        ax.setTextPen(pg.mkPen(TEXT_MUTED))
    plot_item.showGrid(x=True, y=True, alpha=0.18)


def make_plot(title: str = "", x_label: str = "", y_label: str = "") -> pg.PlotWidget:
    """Return a styled dark PlotWidget."""
    widget = pg.PlotWidget(title=title)
    widget.setBackground(BG)
    pi = widget.getPlotItem()
    _style_plot_item(pi)
    if x_label:
        pi.setLabel("bottom", x_label, color=TEXT_MUTED)
    if y_label:
        pi.setLabel("left", y_label, color=TEXT_MUTED)
    return widget


def style_graphics_layout(glw: pg.GraphicsLayoutWidget) -> None:
    """Apply dark styling to all PlotItems inside a GraphicsLayoutWidget."""
    glw.setBackground(BG)
    for item in glw.ci.items:
        if isinstance(item, pg.PlotItem):
            _style_plot_item(item)
