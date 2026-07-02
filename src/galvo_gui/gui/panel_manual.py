"""Connection and motion panels for galvo and parabolic-mirror control."""

from __future__ import annotations

import contextlib
from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
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
from galvo_gui.motion.base import (
    STANDARD_STEP_OPTIONS_NM,
    Z_STEP_OPTIONS_NM,
    GalvoBackend,
)

_DEFAULT_CAL_DIR = Path(__file__).resolve().parents[3] / "config_files" / "cal_files"


class ConnectionPanel(QWidget):
    """Connection settings and lifecycle for the shared backend instance."""

    backend_connected = pyqtSignal(object)   # GalvoBackend
    backend_disconnected = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: GalvoBackend | None = None
        self._settings = QSettings("galvo_gui", "ManualPanel")
        self._build_ui()
        self._restore_settings()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(self._build_connection_group())
        self._log = LogView(self)
        root.addWidget(self._log)
        root.addStretch()

    def _build_connection_group(self) -> QGroupBox:
        grp = QGroupBox("Connection")
        grid = QGridLayout(grp)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("Backend:"), 0, 0)
        self._backend_combo = QComboBox()
        self._backend_combo.addItem("Mock (no hardware)")
        self._backend_combo.addItem("Real (galvo_functions + nea_tools)")
        self._backend_combo.addItem("Canon (GC-211/212 + GB511)")
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        grid.addWidget(self._backend_combo, 0, 1, 1, 2)

        grid.addWidget(QLabel("Host:"), 1, 0)
        self._host_edit = QLineEdit("nea-server")
        grid.addWidget(self._host_edit, 1, 1, 1, 2)

        grid.addWidget(QLabel("Cal files:"), 2, 0)
        self._cal_edit = QLineEdit(str(_DEFAULT_CAL_DIR))
        grid.addWidget(self._cal_edit, 2, 1)
        browse_btn = QPushButton("…")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse_cal)
        grid.addWidget(browse_btn, 2, 2)
        self._cal_row_widgets = [
            grid.itemAtPosition(2, 0).widget(),  # type: ignore[union-attr]
            self._cal_edit,
            browse_btn,
        ]

        grid.addWidget(QLabel("Serial port:"), 3, 0)
        self._serial_port_edit = QLineEdit("")
        grid.addWidget(self._serial_port_edit, 3, 1, 1, 2)

        grid.addWidget(QLabel("Board index:"), 4, 0)
        self._board_index_edit = QLineEdit("0")
        grid.addWidget(self._board_index_edit, 4, 1, 1, 2)

        grid.addWidget(QLabel("Program file:"), 5, 0)
        self._program_file_edit = QLineEdit("")
        self._program_file_edit.setPlaceholderText("blank = config_files/gbdsp.hex")
        grid.addWidget(self._program_file_edit, 5, 1, 1, 2)

        self._canon_row_widgets = [
            grid.itemAtPosition(3, 0).widget(),  # type: ignore[union-attr]
            self._serial_port_edit,
            grid.itemAtPosition(4, 0).widget(),  # type: ignore[union-attr]
            self._board_index_edit,
            grid.itemAtPosition(5, 0).widget(),  # type: ignore[union-attr]
            self._program_file_edit,
        ]

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setProperty("accent", True)
        self._connect_btn.clicked.connect(self._on_connect_toggle)
        grid.addWidget(self._connect_btn, 6, 0, 1, 3)

        self._on_backend_changed(0)
        return grp

    def _on_backend_changed(self, index: int) -> None:
        is_real = index == 1
        is_canon = index == 2
        self._host_edit.setVisible(is_real)
        for widget in self._cal_row_widgets:
            widget.setVisible(is_real)
        for widget in self._canon_row_widgets:
            widget.setVisible(is_canon)

    def _browse_cal(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Select cal_files directory",
            self._cal_edit.text(),
        )
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
        elif index == 1:
            try:
                from galvo_gui.motion.galvo_nea import GALVO_AVAILABLE, GalvoNeaBackend
            except ImportError:
                GALVO_AVAILABLE = False
                GalvoNeaBackend = None  # type: ignore[assignment, misc]
            if not GALVO_AVAILABLE:
                self._log.append_line(
                    "ERROR: galvo_functions / nea_tools not available. Using Mock instead."
                )
                backend = MockGalvoBackend()
                used_mock_fallback = True
            else:
                backend = GalvoNeaBackend(self._cal_edit.text())
        else:
            from galvo_gui.motion.canon.backend import CanonGalvoBackend

            board_index = int(self._board_index_edit.text() or "0")
            program_file = self._program_file_edit.text().strip() or None
            serial_port = self._serial_port_edit.text().strip() or None
            backend = CanonGalvoBackend(
                board_index=board_index,
                program_file=program_file,
                serial_port=serial_port,
            )

        try:
            host = self._host_edit.text().strip() or "nea-server"
            backend.connect(host)
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self._log.append_line(f"Connection failed: {exc}")
            return

        self._backend = backend
        self._backend_combo.setEnabled(False)
        self._connect_btn.setText("Disconnect")
        self._connect_btn.setProperty("accent", False)
        self._connect_btn.style().unpolish(self._connect_btn)  # type: ignore[union-attr]
        self._connect_btn.style().polish(self._connect_btn)    # type: ignore[union-attr]

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
        try:
            self._backend.disconnect()
        except Exception as exc:  # noqa: BLE001
            self._log.append_line(f"Disconnect error: {exc}")
        self._backend = None
        self._backend_combo.setEnabled(True)
        self._connect_btn.setText("Connect")
        self._connect_btn.setProperty("accent", True)
        self._connect_btn.style().unpolish(self._connect_btn)  # type: ignore[union-attr]
        self._connect_btn.style().polish(self._connect_btn)    # type: ignore[union-attr]
        self._log.append_line("Disconnected.")
        self.log_message.emit("Galvo disconnected")
        self.backend_disconnected.emit()

    def _restore_settings(self) -> None:
        s = self._settings
        with contextlib.suppress(Exception):
            self._backend_combo.setCurrentIndex(int(s.value("backend_index", 0)))  # type: ignore[arg-type]
        host = s.value("host", "nea-server")
        if isinstance(host, str):
            self._host_edit.setText(host)
        cal = s.value("cal_path", str(_DEFAULT_CAL_DIR))
        if isinstance(cal, str):
            self._cal_edit.setText(cal)
        serial_port = s.value("canon_serial_port", "")
        if isinstance(serial_port, str):
            self._serial_port_edit.setText(serial_port)
        board_index = s.value("canon_board_index", "0")
        if isinstance(board_index, str):
            self._board_index_edit.setText(board_index)
        program_file = s.value("canon_program_file", "")
        if isinstance(program_file, str):
            self._program_file_edit.setText(program_file)

    def save_settings(self) -> None:
        s = self._settings
        s.setValue("backend_index", self._backend_combo.currentIndex())
        s.setValue("host", self._host_edit.text())
        s.setValue("cal_path", self._cal_edit.text())
        s.setValue("canon_serial_port", self._serial_port_edit.text())
        s.setValue("canon_board_index", self._board_index_edit.text())
        s.setValue("canon_program_file", self._program_file_edit.text())

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self.save_settings()
        if self._backend is not None and self._backend.is_connected():
            self._disconnect()
        super().closeEvent(event)  # type: ignore[misc]


class MotionPanel(QWidget):
    """Manual XY/Z motion controls for a connected backend."""

    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: GalvoBackend | None = None
        self._locked = False
        self._last_position_error: str | None = None
        self._settings = QSettings("galvo_gui", "MotionPanel")
        self._build_ui()
        self._restore_settings()

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._refresh_position)

        self._set_controls_enabled(False, False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(self._build_motion_group())
        root.addWidget(self._build_position_group())
        root.addStretch()

    def _build_motion_group(self) -> QGroupBox:
        grp = QGroupBox("Motion")
        vbox = QVBoxLayout(grp)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("XY step (nm):"))
        self._xy_step_combo = self._build_step_combo(STANDARD_STEP_OPTIONS_NM, "100")
        step_row.addWidget(self._xy_step_combo)
        step_row.addSpacing(12)
        step_row.addWidget(QLabel("Z step (nm):"))
        self._z_step_combo = self._build_step_combo(Z_STEP_OPTIONS_NM, "1000")
        step_row.addWidget(self._z_step_combo)
        step_row.addStretch()
        vbox.addLayout(step_row)

        grid = QGridLayout()
        grid.setSpacing(4)

        self._btn_up = QPushButton("▲")
        self._btn_down = QPushButton("▼")
        self._btn_left = QPushButton("◀")
        self._btn_right = QPushButton("▶")
        self._btn_center = QPushButton("⊙")
        self._btn_z_up = QPushButton("▲")
        self._btn_z_down = QPushButton("▼")

        for btn in (
            self._btn_up,
            self._btn_down,
            self._btn_left,
            self._btn_right,
            self._btn_center,
            self._btn_z_up,
            self._btn_z_down,
        ):
            btn.setFixedSize(48, 36)

        grid.addWidget(self._btn_up, 0, 1)
        grid.addWidget(self._btn_left, 1, 0)
        grid.addWidget(self._btn_center, 1, 1)
        grid.addWidget(self._btn_right, 1, 2)
        grid.addWidget(self._btn_down, 2, 1)
        grid.addWidget(QLabel("Z"), 1, 3)
        grid.addWidget(self._btn_z_up, 0, 4)
        grid.addWidget(self._btn_z_down, 2, 4)

        self._btn_up.clicked.connect(lambda: self._jog_xy(0, 1))
        self._btn_down.clicked.connect(lambda: self._jog_xy(0, -1))
        self._btn_left.clicked.connect(lambda: self._jog_xy(-1, 0))
        self._btn_right.clicked.connect(lambda: self._jog_xy(1, 0))
        self._btn_center.clicked.connect(self._goto_center)
        self._btn_z_up.clicked.connect(lambda: self._jog_z(1))
        self._btn_z_down.clicked.connect(lambda: self._jog_z(-1))

        cross_widget = QWidget()
        cross_widget.setLayout(grid)
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

        grid.addWidget(QLabel("Z (nm):"), 2, 0)
        self._z_label = ReadoutLabel("--")
        grid.addWidget(self._z_label, 2, 1)
        return grp

    def _build_step_combo(self, options: tuple[float, ...], default: str) -> QComboBox:
        combo = QComboBox()
        for step_nm in options:
            combo.addItem(f"{step_nm:g}")
        combo.setCurrentText(default)
        return combo

    def set_backend(self, backend: GalvoBackend) -> None:
        self._backend = backend
        self._apply_step_availability()
        self._refresh_position()
        if not self._locked:
            self._pos_timer.start()

    def clear_backend(self) -> None:
        self._backend = None
        self._pos_timer.stop()
        self._x_label.setText("--")
        self._y_label.setText("--")
        self._z_label.setText("--")
        self._set_controls_enabled(False, False)

    def _jog_xy(self, sign_x: int, sign_y: int) -> None:
        if self._backend is None:
            return
        step_nm = float(self._xy_step_combo.currentText())
        try:
            self._backend.move_relative(sign_x * step_nm, sign_y * step_nm)
            self._refresh_position()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.log_message.emit(f"Move error: {exc}")

    def _jog_z(self, sign_z: int) -> None:
        if self._backend is None:
            return
        step_nm = float(self._z_step_combo.currentText())
        try:
            self._backend.move_z_relative(sign_z * step_nm)
            self._refresh_position()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.log_message.emit(f"Z move error: {exc}")

    def _goto_center(self) -> None:
        if self._backend is None:
            return
        try:
            self._backend.goto_center()
            self._refresh_position()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.log_message.emit(f"Center error: {exc}")

    def _refresh_position(self) -> None:
        if self._backend is None or not self._backend.is_connected():
            return
        try:
            x_nm, y_nm = self._backend.read_xy_nm()
            z_nm = self._backend.read_z_nm()
        except Exception as exc:  # noqa: BLE001
            # Report once per distinct failure instead of spamming the 500 ms
            # timer — but never fail silently: an invisible read error is how
            # a dead board masquerades as "connected but not moving".
            msg = f"Position read error: {exc}"
            if msg != self._last_position_error:
                self._last_position_error = msg
                self.log_message.emit(msg)
            return
        self._last_position_error = None
        self._x_label.setText(f"{x_nm:.0f}")
        self._y_label.setText(f"{y_nm:.0f}")
        self._z_label.setText(f"{z_nm:.0f}")

    def _apply_step_availability(self) -> None:
        if self._backend is None or not self._backend.is_connected():
            self._set_controls_enabled(False, False)
            return

        xy_steps = self._backend.available_xy_steps_nm()
        z_steps = self._backend.available_z_steps_nm()
        self._set_combo_item_enabled(self._xy_step_combo, xy_steps)
        self._set_combo_item_enabled(self._z_step_combo, z_steps)
        self._ensure_combo_selection(self._xy_step_combo, xy_steps)
        self._ensure_combo_selection(self._z_step_combo, z_steps)
        self._set_controls_enabled(bool(xy_steps), bool(z_steps))

    def _set_combo_item_enabled(
        self,
        combo: QComboBox,
        available_steps: tuple[float, ...],
    ) -> None:
        model = combo.model()
        item_fn = getattr(model, "item", None)
        available_labels = {f"{step_nm:g}" for step_nm in available_steps}
        for idx in range(combo.count()):
            item = item_fn(idx) if callable(item_fn) else None
            if item is not None:
                item.setEnabled(combo.itemText(idx) in available_labels)

    def _ensure_combo_selection(
        self,
        combo: QComboBox,
        available_steps: tuple[float, ...],
    ) -> None:
        if not available_steps:
            return
        if combo.currentText() in {f"{step_nm:g}" for step_nm in available_steps}:
            return
        combo.setCurrentText(f"{available_steps[0]:g}")

    def _set_controls_enabled(self, xy_enabled: bool, z_enabled: bool) -> None:
        enable_xy = (
            xy_enabled
            and not self._locked
            and self._backend is not None
            and self._backend.is_connected()
        )
        enable_z = (
            z_enabled
            and not self._locked
            and self._backend is not None
            and self._backend.is_connected()
        )
        for btn in (
            self._btn_up,
            self._btn_down,
            self._btn_left,
            self._btn_right,
            self._btn_center,
        ):
            btn.setEnabled(enable_xy)
        for btn in (self._btn_z_up, self._btn_z_down):
            btn.setEnabled(enable_z)
        self._xy_step_combo.setEnabled(enable_xy)
        self._z_step_combo.setEnabled(enable_z)

    def _restore_settings(self) -> None:
        xy_step = self._settings.value("xy_step_nm", "100")
        z_step = self._settings.value("z_step_nm", "1000")
        if isinstance(xy_step, str):
            self._xy_step_combo.setCurrentText(xy_step)
        if isinstance(z_step, str):
            self._z_step_combo.setCurrentText(z_step)

    def save_settings(self) -> None:
        self._settings.setValue("xy_step_nm", self._xy_step_combo.currentText())
        self._settings.setValue("z_step_nm", self._z_step_combo.currentText())

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self.save_settings()
        self._pos_timer.stop()
        super().closeEvent(event)  # type: ignore[misc]

    def lock_for_scan(self, locked: bool) -> None:
        self._locked = locked
        if locked:
            self._pos_timer.stop()
        elif self._backend is not None and self._backend.is_connected():
            self._pos_timer.start()
        self._apply_step_availability()


ManualPanel = ConnectionPanel
