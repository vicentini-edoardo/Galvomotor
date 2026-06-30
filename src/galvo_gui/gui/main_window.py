"""Main application window: QTabWidget with Manual and Scan tabs."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from galvo_gui.gui.panel_manual import ManualPanel
from galvo_gui.gui.panel_scan import ScanPanel


class MainWindow(QMainWindow):
    """Top-level window: two-tab layout for manual control and raster scan."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Galvo Motor Control")

        self._tabs = QTabWidget(self)
        self.setCentralWidget(self._tabs)

        self._manual = ManualPanel(self)
        self._scan = ScanPanel(self)
        self._tabs.addTab(self._manual, "Manual")
        self._tabs.addTab(self._scan, "Scan")

        # Wire backend connect/disconnect → scan panel
        self._manual.backend_connected.connect(self._scan.set_backend)
        self._manual.backend_disconnected.connect(self._scan.clear_backend)

        # Lock jog controls during scan
        self._scan.running_changed.connect(self._manual.lock_for_scan)

        # Status bar
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)

        self._conn_label = QLabel("● Not connected")
        self._conn_label.setObjectName("connection_label")
        self._conn_label.setProperty("connected", "false")
        self._status_bar.addWidget(self._conn_label)

        self._scan_label = QLabel("Scan: Idle")
        self._scan_label.setObjectName("acquisition_label")
        self._scan_label.setProperty("running", "false")
        self._status_bar.addWidget(self._scan_label)

        self._status_bar.addPermanentWidget(
            QLabel("galvo-gui v0.1"), 0
        )

        # Wire status updates
        self._manual.backend_connected.connect(self._on_connected)
        self._manual.backend_disconnected.connect(self._on_disconnected)
        self._scan.running_changed.connect(self._on_scan_running)
        self._manual.log_message.connect(self._status_bar.showMessage)
        self._scan.log_message.connect(self._status_bar.showMessage)

    # ------------------------------------------------------------------

    def _on_connected(self, _backend: object) -> None:
        self._conn_label.setText("● Connected")
        self._conn_label.setProperty("connected", "true")
        self._conn_label.style().unpolish(self._conn_label)  # type: ignore[union-attr]
        self._conn_label.style().polish(self._conn_label)    # type: ignore[union-attr]

    def _on_disconnected(self) -> None:
        self._conn_label.setText("● Not connected")
        self._conn_label.setProperty("connected", "false")
        self._conn_label.style().unpolish(self._conn_label)  # type: ignore[union-attr]
        self._conn_label.style().polish(self._conn_label)    # type: ignore[union-attr]

    def _on_scan_running(self, running: bool) -> None:
        self._scan_label.setText("Scan: Running" if running else "Scan: Idle")
        self._scan_label.setProperty("running", "true" if running else "false")
        self._scan_label.style().unpolish(self._scan_label)  # type: ignore[union-attr]
        self._scan_label.style().polish(self._scan_label)    # type: ignore[union-attr]
        # Switch to Scan tab automatically when scan starts
        if running:
            self._tabs.setCurrentWidget(self._scan)

    # ------------------------------------------------------------------

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        # Propagate close to panels so they save QSettings and stop workers
        self._scan.closeEvent(event)
        self._manual.closeEvent(event)
        super().closeEvent(event)  # type: ignore[misc]
