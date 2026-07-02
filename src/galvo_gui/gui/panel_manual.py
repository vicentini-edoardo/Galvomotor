"""Connection and motion panels for galvo and parabolic-mirror control."""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QSettings, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtCore import QMetaObject, QObject, pyqtSlot
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


def _stop_thread(thread: QThread) -> None:
    if thread.isRunning():
        thread.quit()
        thread.wait(5000)


class _BackendOpRunner(QObject):
    """Execute blocking backend lifecycle operations on one dedicated thread.

    The neaSNOM SDK is sensitive to thread-affinity: the CLR/DLL state it
    loads on the first connect stays pinned to that OS thread for the rest
    of the process. The runner's thread is therefore created once and kept
    for the whole lifetime of the panel — every connect and disconnect,
    including reconnects after a disconnect, must run on that same thread
    or the next ``nea_tools.connect`` hangs until the app is restarted.
    """

    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._op: Callable[[], None] | None = None

    def set_op(self, op: Callable[[], None]) -> None:
        self._op = op

    @pyqtSlot()
    def run_current_op(self) -> None:
        op = self._op
        self._op = None
        if op is None:
            self.failed.emit("Internal error: no backend operation scheduled.")
            return
        try:
            op()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()


class ConnectionPanel(QWidget):
    """Connection settings and lifecycle for the shared backend instance."""

    backend_connected = pyqtSignal(object)   # GalvoBackend
    backend_disconnected = pyqtSignal()
    log_message = pyqtSignal(str)
    backend_progress = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: GalvoBackend | None = None
        self._op_thread: QThread | None = None
        self._op_runner: _BackendOpRunner | None = None
        self._op_busy = False
        self._op_success_callback: Callable[[], None] | None = None
        self._op_failure_callback: Callable[[str], None] | None = None
        self._pending_backend: GalvoBackend | None = None
        self._pending_name = ""
        self._settings = QSettings("galvo_gui", "ManualPanel")
        self.backend_progress.connect(self._append_backend_progress)
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
        self._board_index_edit = QLineEdit("1")
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
        is_real = index in (1, 2)
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
        if self._op_busy:
            return  # connect/disconnect already in flight
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
                try:
                    backend = GalvoNeaBackend(self._cal_edit.text())
                except Exception as exc:  # noqa: BLE001
                    self._log.append_line(f"Connection failed: {exc}")
                    return
        else:
            from galvo_gui.motion.canon.backend import CanonGalvoBackend

            board_index_text = self._board_index_edit.text().strip()
            board_index = int(board_index_text) if board_index_text else None
            if board_index is not None and board_index <= 0:
                board_index = None
            program_file = self._program_file_edit.text().strip() or None
            serial_port = self._serial_port_edit.text().strip() or None
            try:
                backend = CanonGalvoBackend(
                    self._cal_edit.text(),
                    board_index=board_index,
                    program_file=program_file,
                    serial_port=serial_port,
                )
            except Exception as exc:  # noqa: BLE001
                self._log.append_line(f"Connection failed: {exc}")
                return

        if index == 0:
            self._pending_name = "Mock"
        elif used_mock_fallback:
            self._pending_name = "Mock (fallback)"
        else:
            self._pending_name = "Real" if index == 1 else "Canon"

        connect_target = self._host_edit.text().strip() or "nea-server"
        if hasattr(backend, "set_status_callback"):
            backend.set_status_callback(self.backend_progress.emit)
        self._pending_backend = backend
        self._set_button_state("Connecting…", accent=True, enabled=False)
        self._backend_combo.setEnabled(False)
        self._start_op(lambda: backend.connect(connect_target), self._on_connect_succeeded,
                       self._on_connect_failed)

    def _on_connect_succeeded(self) -> None:
        self._finish_op()
        self._backend = self._pending_backend
        self._pending_backend = None
        self._set_button_state("Disconnect", accent=False, enabled=True)
        self._log.append_line(f"Connected ({self._pending_name} backend).")
        self.log_message.emit(f"Galvo connected ({self._pending_name})")
        self.backend_connected.emit(self._backend)

    def _on_connect_failed(self, message: str) -> None:
        self._finish_op()
        self._pending_backend = None
        self._log.append_line(f"Connection failed: {message}")
        self._set_button_state("Connect", accent=True, enabled=True)
        self._backend_combo.setEnabled(True)

    def _disconnect(self) -> None:
        if self._backend is None:
            return
        backend = self._backend
        # Detach the backend from the rest of the GUI first so the position
        # poller and scan panel stop touching hardware during teardown.
        self._backend = None
        self.backend_disconnected.emit()
        self._set_button_state("Disconnecting…", accent=False, enabled=False)
        self._start_op(backend.disconnect, self._on_disconnect_succeeded,
                       self._on_disconnect_failed)

    def _on_disconnect_succeeded(self) -> None:
        self._finish_op()
        self._log.append_line("Disconnected.")
        self.log_message.emit("Galvo disconnected")
        self._set_button_state("Connect", accent=True, enabled=True)
        self._backend_combo.setEnabled(True)

    def _on_disconnect_failed(self, message: str) -> None:
        self._finish_op()
        self._log.append_line(f"Disconnect error: {message}")
        self.log_message.emit("Galvo disconnected")
        self._set_button_state("Connect", accent=True, enabled=True)
        self._backend_combo.setEnabled(True)

    def _start_op(
        self,
        op: Callable[[], None],
        on_success: Callable[[], None],
        on_failure: Callable[[str], None],
    ) -> None:
        self._ensure_op_thread()
        runner = self._op_runner
        if runner is None:
            on_failure("Internal error: backend worker unavailable.")
            return
        self._op_busy = True
        self._op_success_callback = on_success
        self._op_failure_callback = on_failure
        runner.set_op(op)
        QMetaObject.invokeMethod(
            runner,
            "run_current_op",
            Qt.ConnectionType.QueuedConnection,
        )

    def _finish_op(self) -> None:
        # The op thread is deliberately NOT torn down here: the SDK pins its
        # state to the thread of the first connect, so a reconnect scheduled
        # on a fresh thread hangs. The thread lives until closeEvent.
        self._op_busy = False

    def _ensure_op_thread(self) -> None:
        if self._op_thread is not None and self._op_runner is not None:
            return
        # Unparented on purpose: a QThread owned by the panel is destroyed
        # while still running if the panel is garbage-collected without
        # closeEvent, which aborts the process. The destroyed hook below
        # stops the thread first; the closure keeps the wrapper alive.
        thread = QThread()
        runner = _BackendOpRunner()
        runner.moveToThread(thread)
        runner.succeeded.connect(self._handle_op_succeeded)
        runner.failed.connect(self._handle_op_failed)
        thread.finished.connect(runner.deleteLater)
        self.destroyed.connect(lambda: _stop_thread(thread))
        thread.start()
        self._op_thread = thread
        self._op_runner = runner

    def _teardown_op_thread(self) -> None:
        thread = self._op_thread
        self._op_thread = None
        self._op_runner = None
        if thread is None:
            return
        with contextlib.suppress(RuntimeError):
            _stop_thread(thread)

    @pyqtSlot()
    def _handle_op_succeeded(self) -> None:
        callback = self._op_success_callback
        self._op_success_callback = None
        self._op_failure_callback = None
        if callback is not None:
            callback()

    @pyqtSlot(str)
    def _handle_op_failed(self, message: str) -> None:
        callback = self._op_failure_callback
        self._op_success_callback = None
        self._op_failure_callback = None
        if callback is not None:
            callback(message)

    @pyqtSlot(str)
    def _append_backend_progress(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self._log.append_line(line)
        self.log_message.emit(message)

    def _set_button_state(self, text: str, accent: bool, enabled: bool) -> None:
        self._connect_btn.setText(text)
        self._connect_btn.setEnabled(enabled)
        self._connect_btn.setProperty("accent", accent)
        self._connect_btn.style().unpolish(self._connect_btn)  # type: ignore[union-attr]
        self._connect_btn.style().polish(self._connect_btn)    # type: ignore[union-attr]

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
        board_index = s.value("canon_board_index", "1")
        if isinstance(board_index, str):
            self._board_index_edit.setText("1" if board_index.strip() == "0" else board_index)
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
        backend = self._backend
        self._backend = None
        if backend is not None and backend.is_connected():
            # A connected backend implies no op is in flight, so the runner is
            # idle. Tear the session down ON the op thread (the SDK must be
            # closed by the thread that opened it); the blocking invoke
            # returns once disconnect has run, and any error goes to the
            # failed signal, which has no callbacks armed at shutdown.
            runner = self._op_runner
            thread = self._op_thread
            if runner is not None and thread is not None and thread.isRunning():
                runner.set_op(backend.disconnect)
                QMetaObject.invokeMethod(
                    runner,
                    "run_current_op",
                    Qt.ConnectionType.BlockingQueuedConnection,
                )
            else:
                with contextlib.suppress(Exception):
                    backend.disconnect()
            self.backend_disconnected.emit()
        # quit() is honoured only after any in-flight op returns, so this also
        # waits out a connect/disconnect that is still running.
        self._teardown_op_thread()
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
        self._home_x_nm = 0.0
        self._home_y_nm = 0.0
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
        root.addStretch()

    def _build_motion_group(self) -> QGroupBox:
        grp = QGroupBox("Motion")
        vbox = QVBoxLayout(grp)
        vbox.setSpacing(12)

        split = QHBoxLayout()
        split.setSpacing(12)
        split.addWidget(self._build_xy_cluster(), 3)
        split.addWidget(self._build_z_cluster(), 2)
        vbox.addLayout(split)
        return grp

    def _build_xy_cluster(self) -> QWidget:
        cluster = QWidget()
        cluster.setObjectName("MotionCluster")
        cluster.setProperty("clusterRole", "xy")

        outer = QVBoxLayout(cluster)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Galvomotor XY")
        title.setObjectName("MotionClusterTitle")
        header.addWidget(title)
        header.addStretch()
        step_label = QLabel("Step (nm)")
        step_label.setObjectName("MotionInlineLabel")
        header.addWidget(step_label)
        self._xy_step_combo = self._build_step_combo(STANDARD_STEP_OPTIONS_NM, "100")
        header.addWidget(self._xy_step_combo)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)

        grid = QGridLayout()
        grid.setSpacing(4)

        self._btn_up = QPushButton("▲")
        self._btn_down = QPushButton("▼")
        self._btn_left = QPushButton("◀")
        self._btn_right = QPushButton("▶")
        self._btn_center = QPushButton("⊙")

        for btn in (
            self._btn_up,
            self._btn_down,
            self._btn_left,
            self._btn_right,
            self._btn_center,
        ):
            btn.setFixedSize(52, 40)

        grid.addWidget(self._btn_up, 0, 1)
        grid.addWidget(self._btn_left, 1, 0)
        grid.addWidget(self._btn_center, 1, 1)
        grid.addWidget(self._btn_right, 1, 2)
        grid.addWidget(self._btn_down, 2, 1)

        self._btn_up.clicked.connect(lambda: self._jog_xy(0, 1))
        self._btn_down.clicked.connect(lambda: self._jog_xy(0, -1))
        self._btn_left.clicked.connect(lambda: self._jog_xy(-1, 0))
        self._btn_right.clicked.connect(lambda: self._jog_xy(1, 0))
        self._btn_center.clicked.connect(self._goto_center)

        pad = QWidget()
        pad.setLayout(grid)
        body.addWidget(pad)

        self._x_label = self._build_motion_readout("--")
        self._y_label = self._build_motion_readout("--")
        self._home_label = self._build_motion_readout("0, 0", home=True)
        self._btn_set_home = QPushButton("Set Home")
        self._btn_set_home.clicked.connect(self._set_home)

        readouts = QVBoxLayout()
        readouts.setSpacing(8)
        readouts.addWidget(self._build_readout_column("X live", self._x_label))
        readouts.addWidget(self._build_readout_column("Y live", self._y_label))
        readouts.addSpacing(2)
        readouts.addWidget(self._build_readout_column("Home", self._home_label))
        readouts.addWidget(self._btn_set_home, alignment=Qt.AlignmentFlag.AlignLeft)
        readouts.addStretch()
        body.addLayout(readouts, 1)

        outer.addLayout(body)
        return cluster

    def _build_z_cluster(self) -> QWidget:
        cluster = QWidget()
        cluster.setObjectName("MotionCluster")
        cluster.setProperty("clusterRole", "z")

        outer = QVBoxLayout(cluster)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("neaSNOM Z")
        title.setObjectName("MotionClusterTitle")
        header.addWidget(title)
        header.addStretch()
        step_label = QLabel("Step (nm)")
        step_label.setObjectName("MotionInlineLabel")
        header.addWidget(step_label)
        self._z_step_combo = self._build_step_combo(Z_STEP_OPTIONS_NM, "1000")
        header.addWidget(self._z_step_combo)
        outer.addLayout(header)

        self._btn_z_up = QPushButton("▲")
        self._btn_z_down = QPushButton("▼")
        for btn in (self._btn_z_up, self._btn_z_down):
            btn.setFixedSize(52, 40)

        self._btn_z_up.clicked.connect(lambda: self._jog_z(1))
        self._btn_z_down.clicked.connect(lambda: self._jog_z(-1))

        body = QHBoxLayout()
        body.setSpacing(16)

        z_buttons = QVBoxLayout()
        z_buttons.setSpacing(6)
        z_buttons.addWidget(self._btn_z_up)
        z_buttons.addWidget(self._btn_z_down)
        z_buttons.addStretch()
        body.addLayout(z_buttons)

        self._z_label = self._build_motion_readout("--")
        z_readout = QVBoxLayout()
        z_readout.setSpacing(8)
        z_readout.addWidget(self._build_readout_column("Z live", self._z_label))
        z_readout.addStretch()
        body.addLayout(z_readout, 1)

        outer.addLayout(body)
        return cluster

    def _build_step_combo(self, options: tuple[float, ...], default: str) -> QComboBox:
        combo = QComboBox()
        for step_nm in options:
            combo.addItem(f"{step_nm:g}")
        combo.setCurrentText(default)
        return combo

    def _build_motion_readout(self, text: str, *, home: bool = False) -> ReadoutLabel:
        label = ReadoutLabel(text)
        label.setProperty("motionReadout", True)
        if home:
            label.setProperty("homeReadout", True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return label

    def _build_readout_column(self, title: str, value_label: ReadoutLabel) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        caption = QLabel(title)
        caption.setObjectName("MotionReadoutCaption")
        layout.addWidget(caption)
        layout.addWidget(value_label)
        return widget

    def set_backend(self, backend: GalvoBackend) -> None:
        self._backend = backend
        try:
            self._backend.set_home(self._home_x_nm, self._home_y_nm)
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(f"Home restore error: {exc}")
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

    def _set_home(self) -> None:
        if self._backend is None:
            return
        try:
            self._home_x_nm, self._home_y_nm = self._backend.set_home()
            self._update_home_label()
            self.save_settings()
            self._refresh_position()
            self.log_message.emit(
                f"Home set to ({self._home_x_nm:.0f}, {self._home_y_nm:.0f}) nm"
            )
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.log_message.emit(f"Set home error: {exc}")

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
            self._btn_set_home,
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
        with contextlib.suppress(Exception):
            self._home_x_nm = float(self._settings.value("home_x_nm", 0.0))
        with contextlib.suppress(Exception):
            self._home_y_nm = float(self._settings.value("home_y_nm", 0.0))
        self._update_home_label()

    def save_settings(self) -> None:
        self._settings.setValue("xy_step_nm", self._xy_step_combo.currentText())
        self._settings.setValue("z_step_nm", self._z_step_combo.currentText())
        self._settings.setValue("home_x_nm", self._home_x_nm)
        self._settings.setValue("home_y_nm", self._home_y_nm)

    def _update_home_label(self) -> None:
        self._home_label.setText(f"{self._home_x_nm:.0f}, {self._home_y_nm:.0f}")

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
