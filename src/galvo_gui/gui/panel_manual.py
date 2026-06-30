"""Manual galvo control panel: cross controller, connection, live position."""

from __future__ import annotations

from PyQt6.QtCore import QSettings, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from galvo_gui.gui.widgets import LogView, ReadoutLabel
from galvo_gui.motion.base import GalvoBackend, GalvoError


class ManualPanel(QWidget):
    """Manual galvo jog + connection panel (Tab 1).

    Emits:
        backend_connected(GalvoBackend): a connected backend is ready for use
        backend_disconnected(): backend has been disconnected
        log_message(str): status message for the status bar
    """

    backend_connected = pyqtSignal(object)   # GalvoBackend
    backend_disconnected = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: GalvoBackend | None = None
        self._settings = QSettings("galvo_gui", "ManualPanel")
        self._build_ui()
        self._restore_settings()

        # 2 Hz position refresh timer
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._refresh_position)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        root.addWidget(self._build_connection_group())
        root.addWidget(self._build_cross_group())
        root.addWidget(self._build_position_group())

        self._log = LogView(self)
        root.addWidget(self._log)
        root.addStretch()

        self._set_controls_enabled(False)

    def _build_connection_group(self) -> QGroupBox:
        grp = QGroupBox("Connection")
        grid = QGridLayout(grp)
        grid.setColumnStretch(1, 1)

        # Backend selector
        grid.addWidget(QLabel("Backend:"), 0, 0)
        self._backend_combo = QComboBox()
        self._backend_combo.addItem("Mock (no hardware)")
        self._backend_combo.addItem("Real (galvo_functions + nea_tools)")
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        grid.addWidget(self._backend_combo, 0, 1, 1, 2)

        # neaSNOM host
        grid.addWidget(QLabel("Host:"), 1, 0)
        self._host_edit = QLineEdit("nea-server")
        grid.addWidget(self._host_edit, 1, 1, 1, 2)

        # Cal files path (real backend only)
        grid.addWidget(QLabel("Cal files:"), 2, 0)
        self._cal_edit = QLineEdit("galvomotor/cal_files")
        grid.addWidget(self._cal_edit, 2, 1)
        browse_btn = QPushButton("…")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse_cal)
        grid.addWidget(browse_btn, 2, 2)
        self._cal_row_widgets = [self._cal_edit, browse_btn,
                                  grid.itemAtPosition(2, 0).widget()]  # type: ignore[union-attr]

        # Connect / Disconnect button
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setProperty("accent", True)
        self._connect_btn.clicked.connect(self._on_connect_toggle)
        grid.addWidget(self._connect_btn, 3, 0, 1, 3)

        self._on_backend_changed(0)  # set initial host/cal visibility
        return grp

    def _build_cross_group(self) -> QGroupBox:
        grp = QGroupBox("Manual Control")
        vbox = QVBoxLayout(grp)

        # Step size
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step (nm):"))
        self._step_spin = QDoubleSpinBox()
        self._step_spin.setRange(1.0, 100_000.0)
        self._step_spin.setValue(100.0)
        self._step_spin.setDecimals(0)
        self._step_spin.setSingleStep(100.0)
        step_row.addWidget(self._step_spin)
        step_row.addStretch()
        vbox.addLayout(step_row)

        # Cross buttons
        cross = QGridLayout()
        cross.setSpacing(4)

        self._btn_up = QPushButton("▲")
        self._btn_down = QPushButton("▼")
        self._btn_left = QPushButton("◀")
        self._btn_right = QPushButton("▶")
        self._btn_center = QPushButton("⊙")

        for btn in (self._btn_up, self._btn_down, self._btn_left,
                    self._btn_right, self._btn_center):
            btn.setFixedSize(48, 36)

        cross.addWidget(self._btn_up,    0, 1)
        cross.addWidget(self._btn_left,  1, 0)
        cross.addWidget(self._btn_center, 1, 1)
        cross.addWidget(self._btn_right,  1, 2)
        cross.addWidget(self._btn_down,   2, 1)

        self._btn_up.clicked.connect(lambda: self._jog(0, 1))
        self._btn_down.clicked.connect(lambda: self._jog(0, -1))
        self._btn_left.clicked.connect(lambda: self._jog(-1, 0))
        self._btn_right.clicked.connect(lambda: self._jog(1, 0))
        self._btn_center.clicked.connect(self._goto_center)

        cross_widget = QWidget()
        cross_widget.setLayout(cross)
        vbox.addWidget(cross_widget)

        return grp

    def _build_position_group(self) -> QGroupBox:
        grp = QGroupBox("Position")
        grid = QGridLayout(grp)

        grid.addWidget(QLabel("X (nm):"), 0, 0)
        self._x_label = ReadoutLabel("--")
        grid.addWidget(self._x_label, 0, 1)

        grid.addWidget(QLabel("Y (nm):"), 1, 0)
        self._y_label = ReadoutLabel("--")
        grid.addWidget(self._y_label, 1, 1)

        return grp

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _on_backend_changed(self, index: int) -> None:
        is_real = (index == 1)
        self._host_edit.setVisible(is_real)
        for w in self._cal_row_widgets:
            w.setVisible(is_real)

    def _browse_cal(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select cal_files directory",
                                                self._cal_edit.text())
        if path:
            self._cal_edit.setText(path)

    def _on_connect_toggle(self) -> None:
        if self._backend is not None and self._backend.is_connected():
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        from galvo_gui.motion.mock import MockGalvoBackend

        index = self._backend_combo.currentIndex()
        used_mock_fallback = False
        if index == 0:
            backend: GalvoBackend = MockGalvoBackend()
        else:
            try:
                from galvo_gui.motion.galvo_nea import GALVO_AVAILABLE, GalvoNeaBackend
            except ImportError:
                GALVO_AVAILABLE = False
                GalvoNeaBackend = None  # type: ignore[assignment, misc]
            if not GALVO_AVAILABLE:
                self._log.append_line(
                    "ERROR: galvo_functions / nea_tools not available. "
                    "Using Mock instead."
                )
                backend = MockGalvoBackend()
                used_mock_fallback = True
            else:
                backend = GalvoNeaBackend(self._cal_edit.text())

        try:
            host = self._host_edit.text().strip() or "nea-server"
            backend.connect(host)
        except GalvoError as exc:
            self._log.append_line(f"Connection failed: {exc}")
            return

        self._backend = backend
        self._connect_btn.setText("Disconnect")
        self._connect_btn.setProperty("accent", False)
        self._connect_btn.setProperty("pending", False)
        self._connect_btn.style().unpolish(self._connect_btn)  # type: ignore[union-attr]
        self._connect_btn.style().polish(self._connect_btn)    # type: ignore[union-attr]
        self._backend_combo.setEnabled(False)
        self._set_controls_enabled(True)
        self._pos_timer.start()
        self._refresh_position()
        if index == 0:
            name = "Mock"
        elif used_mock_fallback:
            name = "Mock (fallback)"
        else:
            name = "Real"
        self._log.append_line(f"Connected ({name} backend).")
        self.log_message.emit(f"Galvo connected ({name})")
        self.backend_connected.emit(self._backend)

    def _disconnect(self) -> None:
        if self._backend is None:
            return
        self._pos_timer.stop()
        try:
            self._backend.disconnect()
        except Exception as exc:  # noqa: BLE001
            self._log.append_line(f"Disconnect error: {exc}")
        self._backend = None
        self._connect_btn.setText("Connect")
        self._connect_btn.setProperty("accent", True)
        self._connect_btn.style().unpolish(self._connect_btn)  # type: ignore[union-attr]
        self._connect_btn.style().polish(self._connect_btn)    # type: ignore[union-attr]
        self._backend_combo.setEnabled(True)
        self._set_controls_enabled(False)
        self._x_label.setText("--")
        self._y_label.setText("--")
        self._log.append_line("Disconnected.")
        self.log_message.emit("Galvo disconnected")
        self.backend_disconnected.emit()

    # ------------------------------------------------------------------
    # Jog
    # ------------------------------------------------------------------

    def _jog(self, sign_x: int, sign_y: int) -> None:
        if self._backend is None:
            return
        step = self._step_spin.value()
        try:
            self._backend.move_relative(sign_x * step, sign_y * step)
            self._refresh_position()
        except GalvoError as exc:
            self._log.append_line(f"Move error: {exc}")

    def _goto_center(self) -> None:
        if self._backend is None:
            return
        try:
            self._backend.goto_center()
            self._refresh_position()
        except GalvoError as exc:
            self._log.append_line(f"Center error: {exc}")

    def _refresh_position(self) -> None:
        if self._backend is None or not self._backend.is_connected():
            return
        try:
            x, y = self._backend.read_xy_nm()
            self._x_label.setText(f"{x:.0f}")
            self._y_label.setText(f"{y:.0f}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Enable/disable controls
    # ------------------------------------------------------------------

    def _set_controls_enabled(self, enabled: bool) -> None:
        for btn in (self._btn_up, self._btn_down, self._btn_left,
                    self._btn_right, self._btn_center):
            btn.setEnabled(enabled)
        self._step_spin.setEnabled(enabled)

    # ------------------------------------------------------------------
    # QSettings persistence
    # ------------------------------------------------------------------

    def _restore_settings(self) -> None:
        import contextlib
        s = self._settings
        v = s.value("backend_index", 0)
        with contextlib.suppress(Exception):
            self._backend_combo.setCurrentIndex(int(v))  # type: ignore[arg-type]
        host = s.value("host", "nea-server")
        if isinstance(host, str):
            self._host_edit.setText(host)
        cal = s.value("cal_path", "galvomotor/cal_files")
        if isinstance(cal, str):
            self._cal_edit.setText(cal)
        step = s.value("step_nm", 100.0)
        with contextlib.suppress(Exception):
            self._step_spin.setValue(float(step))  # type: ignore[arg-type]

    def save_settings(self) -> None:
        s = self._settings
        s.setValue("backend_index", self._backend_combo.currentIndex())
        s.setValue("host", self._host_edit.text())
        s.setValue("cal_path", self._cal_edit.text())
        s.setValue("step_nm", self._step_spin.value())

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self.save_settings()
        if self._backend is not None and self._backend.is_connected():
            self._disconnect()
        super().closeEvent(event)  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Public — called from MainWindow on scan start/stop
    # ------------------------------------------------------------------

    def lock_for_scan(self, locked: bool) -> None:
        """Disable jog controls during an active scan."""
        self._set_controls_enabled(not locked)
        if locked:
            self._pos_timer.stop()
        elif self._backend is not None and self._backend.is_connected():
            self._pos_timer.start()
