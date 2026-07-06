"""Connection and motion panels for galvo and neaSNOM control.

The Connection tab holds two independent connections — one for the neaSNOM
(parabolic-mirror Z axis + optical signal) and one for the galvomotor (XY
stage). Each has its own Connect/Disconnect button and runs its blocking
lifecycle on a dedicated, persistent worker thread (the SDKs are thread-affine).
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QMetaObject, QObject, QSettings, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QDoubleValidator
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from galvo_gui.gui.widgets import LogView, ReadoutLabel
from galvo_gui.motion.base import (
    STANDARD_STEP_OPTIONS_PULSES,
    Z_STEP_OPTIONS_NM,
    GalvoBackend,
    NeaBackend,
)

_DEFAULT_CAL_DIR = Path(__file__).resolve().parents[3] / "config_files" / "cal_files"
_MOVE_LOG_PATH = Path(__file__).resolve().parents[3] / "config_files" / "move_history.log"
_MOVE_LOG_LIMIT = 100


def _stop_thread(thread: QThread) -> None:
    if thread.isRunning():
        thread.quit()
        thread.wait(5000)


def _safe_stop_thread(thread: QThread) -> None:
    # The QThread's C++ object may already be gone during interpreter teardown.
    with contextlib.suppress(RuntimeError):
        _stop_thread(thread)


def _append_move_history(message: str) -> None:
    _MOVE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if _MOVE_LOG_PATH.exists():
        with _MOVE_LOG_PATH.open("r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    lines.append(message)
    lines = lines[-_MOVE_LOG_LIMIT:]
    with _MOVE_LOG_PATH.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        if lines:
            fh.write("\n")


def _format_move_diag_fields(diag: dict[str, int | float]) -> str:
    if not diag:
        return ""
    parts: list[str] = []
    for key in (
        "requested_step_x",
        "requested_step_y",
        "before_x_read",
        "before_y_read",
        "target_x_read",
        "target_y_read",
        "target_x_goto",
        "target_y_goto",
        "last_cmd_gx_before",
        "last_cmd_gy_before",
        "cmd_gx_sent",
        "cmd_gy_sent",
        "after_x_read",
        "after_y_read",
        "after_x_goto_equiv",
        "after_y_goto_equiv",
        "x_error_pulses",
        "y_error_pulses",
    ):
        if key not in diag:
            continue
        value = diag[key]
        if isinstance(value, float):
            rendered = f"{value:.0f}" if value.is_integer() else f"{value:g}"
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return f" {' '.join(parts)}" if parts else ""


class _BackendOpRunner(QObject):
    """Execute blocking backend lifecycle operations on one dedicated thread.

    The neaSNOM SDK is sensitive to thread-affinity: the CLR/DLL state it
    loads on the first connect stays pinned to that OS thread for the rest
    of the process. The runner's thread is therefore created once and kept
    for the whole lifetime of the section — every connect and disconnect,
    including reconnects after a disconnect, must run on that same thread
    or the next ``nea_tools.connect`` hangs until the app is restarted.
    """

    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._op: Callable[[], None] | None = None
        self._last_result: object | None = None

    def set_op(self, op: Callable[[], None]) -> None:
        self._op = op

    def take_result(self) -> object | None:
        result = self._last_result
        self._last_result = None
        return result

    @pyqtSlot()
    def run_current_op(self) -> None:
        op = self._op
        self._op = None
        self._last_result = None
        if op is None:
            self.failed.emit("Internal error: no backend operation scheduled.")
            return
        try:
            self._last_result = op()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()


class _ConnectionSection(QGroupBox):
    """One connect/disconnect lifecycle for a single backend (galvo or neaSNOM).

    Subclasses provide the connection-specific fields and backend factory.
    The blocking connect/disconnect runs on a dedicated persistent worker
    thread so the GUI stays responsive and the thread-affine SDK is honoured.
    """

    connected = pyqtSignal(object)      # backend instance
    disconnected = pyqtSignal()
    message = pyqtSignal(str)           # log/status line

    def __init__(self, title: str, settings_group: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._backend: object | None = None
        self._op_thread: QThread | None = None
        self._op_runner: _BackendOpRunner | None = None
        self._op_busy = False
        self._op_success_callback: Callable[[], None] | None = None
        self._op_failure_callback: Callable[[str], None] | None = None
        self._pending_backend: object | None = None
        self._pending_name = ""
        self._settings = QSettings("galvo_gui", settings_group)
        self._backend_progress = _ProgressRelay(self)
        self._backend_progress.progress.connect(self._append_backend_progress)
        self._build_ui()
        self._restore_settings()

    # -- overridable hooks ---------------------------------------------

    def _build_fields(self, grid: QGridLayout) -> int:
        """Add connection fields; return the next free grid row."""
        raise NotImplementedError

    def _make_backend(self) -> tuple[object | None, str]:
        """Build the backend for the current settings. Return (backend, name).

        Return (None, "") after logging when the backend cannot be built.
        """
        raise NotImplementedError

    def _connect_target(self) -> str:
        return ""

    def _set_fields_enabled(self, enabled: bool) -> None:
        """Enable/disable configuration fields while an op is in flight."""

    def _restore_settings(self) -> None:  # pragma: no cover - overridden
        pass

    def save_settings(self) -> None:  # pragma: no cover - overridden
        pass

    def _build_extra_controls(self, grid: QGridLayout, row: int) -> int:
        return row

    def _after_connection_state_changed(self) -> None:
        pass

    # -- UI ------------------------------------------------------------

    def _build_ui(self) -> None:
        grid = QGridLayout(self)
        grid.setColumnStretch(1, 1)
        row = self._build_fields(grid)
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setProperty("accent", True)
        self._connect_btn.clicked.connect(self._on_connect_toggle)
        grid.addWidget(self._connect_btn, row, 0, 1, 3)
        self._build_extra_controls(grid, row + 1)

    def is_backend_connected(self) -> bool:
        backend = self._backend
        return backend is not None and backend.is_connected()  # type: ignore[attr-defined]

    # -- connect / disconnect flow -------------------------------------

    def _on_connect_toggle(self) -> None:
        if self._op_busy:
            return  # connect/disconnect already in flight
        if self.is_backend_connected():
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        backend, name = self._make_backend()
        if backend is None:
            return
        self._pending_name = name
        connect_target = self._connect_target()
        if hasattr(backend, "set_status_callback"):
            backend.set_status_callback(self._backend_progress.progress.emit)  # type: ignore[attr-defined]
        self._pending_backend = backend
        self._set_button_state("Connecting…", accent=True, enabled=False)
        self._set_fields_enabled(False)
        self._after_connection_state_changed()
        self._start_op(
            lambda: backend.connect(connect_target),  # type: ignore[attr-defined]
            self._on_connect_succeeded,
            self._on_connect_failed,
        )

    def _on_connect_succeeded(self) -> None:
        self._finish_op()
        self._backend = self._pending_backend
        self._pending_backend = None
        self._set_button_state("Disconnect", accent=False, enabled=True)
        self.message.emit(f"Connected ({self._pending_name}).")
        self._after_connection_state_changed()
        self.connected.emit(self._backend)

    def _on_connect_failed(self, message: str) -> None:
        self._finish_op()
        self._pending_backend = None
        self.message.emit(f"Connection failed: {message}")
        self._set_button_state("Connect", accent=True, enabled=True)
        self._set_fields_enabled(True)
        self._after_connection_state_changed()

    def _disconnect(self) -> None:
        backend = self._backend
        if backend is None:
            return
        # Detach the backend from the rest of the GUI first so the position
        # poller and scan panel stop touching hardware during teardown.
        self._backend = None
        self.disconnected.emit()
        self._set_button_state("Disconnecting…", accent=False, enabled=False)
        self._after_connection_state_changed()
        self._start_op(backend.disconnect, self._on_disconnect_succeeded,  # type: ignore[attr-defined]
                       self._on_disconnect_failed)

    def _on_disconnect_succeeded(self) -> None:
        self._finish_op()
        self.message.emit("Disconnected.")
        self._set_button_state("Connect", accent=True, enabled=True)
        self._set_fields_enabled(True)
        self._after_connection_state_changed()

    def _on_disconnect_failed(self, message: str) -> None:
        self._finish_op()
        self.message.emit(f"Disconnect error: {message}")
        self._set_button_state("Connect", accent=True, enabled=True)
        self._set_fields_enabled(True)
        self._after_connection_state_changed()

    # -- op thread machinery -------------------------------------------

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
        # on a fresh thread hangs. The thread lives until shutdown.
        self._op_busy = False

    def _ensure_op_thread(self) -> None:
        if self._op_thread is not None and self._op_runner is not None:
            return
        # Unparented on purpose: a QThread owned by the section is destroyed
        # while still running if the section is garbage-collected without
        # shutdown, which aborts the process. The destroyed hook below
        # stops the thread first; the closure keeps the wrapper alive.
        thread = QThread()
        runner = _BackendOpRunner()
        runner.moveToThread(thread)
        runner.succeeded.connect(self._handle_op_succeeded)
        runner.failed.connect(self._handle_op_failed)
        thread.finished.connect(runner.deleteLater)
        # Safety net for GC-without-shutdown; suppress the RuntimeError raised
        # if the C++ QThread has already been deleted by the time this fires.
        self.destroyed.connect(lambda: _safe_stop_thread(thread))
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
        self.message.emit(f"[{timestamp}] {message}")

    def _take_runner_result(self) -> object | None:
        runner = self._op_runner
        return None if runner is None else runner.take_result()

    def _set_button_state(self, text: str, accent: bool, enabled: bool) -> None:
        self._connect_btn.setText(text)
        self._connect_btn.setEnabled(enabled)
        self._connect_btn.setProperty("accent", accent)
        self._connect_btn.style().unpolish(self._connect_btn)  # type: ignore[union-attr]
        self._connect_btn.style().polish(self._connect_btn)    # type: ignore[union-attr]

    # -- shutdown ------------------------------------------------------

    def shutdown(self) -> None:
        self.save_settings()
        backend = self._backend
        self._backend = None
        if backend is not None and backend.is_connected():  # type: ignore[attr-defined]
            # A connected backend implies no op is in flight, so the runner is
            # idle. Tear the session down ON the op thread (the SDK must be
            # closed by the thread that opened it); the blocking invoke
            # returns once disconnect has run, and any error goes to the
            # failed signal, which has no callbacks armed at shutdown.
            runner = self._op_runner
            thread = self._op_thread
            if runner is not None and thread is not None and thread.isRunning():
                runner.set_op(backend.disconnect)  # type: ignore[attr-defined]
                QMetaObject.invokeMethod(
                    runner,
                    "run_current_op",
                    Qt.ConnectionType.BlockingQueuedConnection,
                )
            else:
                with contextlib.suppress(Exception):
                    backend.disconnect()  # type: ignore[attr-defined]
            self.disconnected.emit()
        # quit() is honoured only after any in-flight op returns, so this also
        # waits out a connect/disconnect that is still running.
        self._teardown_op_thread()


class _ProgressRelay(QObject):
    """Marshals backend status-callback strings onto the GUI thread."""

    progress = pyqtSignal(str)


class NeaConnectionSection(_ConnectionSection):
    """neaSNOM connection: parabolic-mirror Z axis + optical signal readout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("neaSNOM (Z axis + optical signal)", "NeaConnection", parent)

    def _build_fields(self, grid: QGridLayout) -> int:
        grid.addWidget(QLabel("Host:"), 0, 0)
        self._host_edit = QLineEdit("nea-server")
        grid.addWidget(self._host_edit, 0, 1, 1, 2)
        return 1

    def _connect_target(self) -> str:
        return self._host_edit.text().strip() or "nea-server"

    def _set_fields_enabled(self, enabled: bool) -> None:
        self._host_edit.setEnabled(enabled)

    def _make_backend(self) -> tuple[object | None, str]:
        from galvo_gui.motion.galvo_nea import NEA_AVAILABLE, RealNeaBackend
        from galvo_gui.motion.mock import MockNeaBackend

        if not NEA_AVAILABLE:
            self.message.emit(
                "nea_tools not available. Using a simulated neaSNOM instead."
            )
            return MockNeaBackend(), "Simulated neaSNOM"
        try:
            return RealNeaBackend(), "neaSNOM"
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Connection failed: {exc}")
            return None, ""

    def _restore_settings(self) -> None:
        host = self._settings.value("host", "nea-server")
        if isinstance(host, str):
            self._host_edit.setText(host)

    def save_settings(self) -> None:
        self._settings.setValue("host", self._host_edit.text())


class GalvoConnectionSection(_ConnectionSection):
    """Galvomotor connection: XY stage. Carries the driver-mode selector."""

    # Combo index → descriptive driver mode
    MODE_SIMULATED = 0
    MODE_GB511 = 1
    MODE_CANON = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Galvomotor (XY stage)", "GalvoConnection", parent)

    def _build_fields(self, grid: QGridLayout) -> int:
        grid.addWidget(QLabel("Driver:"), 0, 0)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Simulated galvo (no hardware)")
        self._mode_combo.addItem("GB511 board (galvo_functions)")
        self._mode_combo.addItem("Canon GC-211/212 (GB511 + RS-232 high-speed)")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        grid.addWidget(self._mode_combo, 0, 1, 1, 2)

        grid.addWidget(QLabel("Cal files:"), 1, 0)
        self._cal_edit = QLineEdit(str(_DEFAULT_CAL_DIR))
        grid.addWidget(self._cal_edit, 1, 1)
        browse_btn = QPushButton("…")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse_cal)
        grid.addWidget(browse_btn, 1, 2)
        self._cal_row_widgets = [
            grid.itemAtPosition(1, 0).widget(),  # type: ignore[union-attr]
            self._cal_edit,
            browse_btn,
        ]

        grid.addWidget(QLabel("Serial port:"), 2, 0)
        self._serial_port_edit = QLineEdit("")
        grid.addWidget(self._serial_port_edit, 2, 1, 1, 2)

        grid.addWidget(QLabel("Board index:"), 3, 0)
        self._board_index_edit = QLineEdit("1")
        grid.addWidget(self._board_index_edit, 3, 1, 1, 2)

        grid.addWidget(QLabel("Program file:"), 4, 0)
        self._program_file_edit = QLineEdit("")
        self._program_file_edit.setPlaceholderText("blank = config_files/gbdsp.hex")
        grid.addWidget(self._program_file_edit, 4, 1, 1, 2)

        self._canon_row_widgets = [
            grid.itemAtPosition(2, 0).widget(),  # type: ignore[union-attr]
            self._serial_port_edit,
            grid.itemAtPosition(3, 0).widget(),  # type: ignore[union-attr]
            self._board_index_edit,
            grid.itemAtPosition(4, 0).widget(),  # type: ignore[union-attr]
            self._program_file_edit,
        ]
        self._on_mode_changed(0)
        return 5

    def _build_extra_controls(self, grid: QGridLayout, row: int) -> int:
        controls = QWidget(self)
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        self._offset_checkbox = QCheckBox("Apply offset correction")
        self._offset_checkbox.toggled.connect(self._on_offset_checkbox_toggled)
        layout.addWidget(self._offset_checkbox)
        self._rerun_calibration_btn = QPushButton("Run calibration at current position")
        self._rerun_calibration_btn.clicked.connect(self._rerun_offset_calibration)
        layout.addWidget(self._rerun_calibration_btn)
        layout.addStretch()
        grid.addWidget(controls, row, 0, 1, 3)
        return row + 1

    def _on_mode_changed(self, index: int) -> None:
        is_real = index in (self.MODE_GB511, self.MODE_CANON)
        is_canon = index == self.MODE_CANON
        for widget in self._cal_row_widgets:
            widget.setVisible(is_real)
        for widget in self._canon_row_widgets:
            widget.setVisible(is_canon)
        self._update_calibration_controls()

    def _browse_cal(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Select cal_files directory",
            self._cal_edit.text(),
        )
        if path:
            self._cal_edit.setText(path)

    def _set_fields_enabled(self, enabled: bool) -> None:
        self._mode_combo.setEnabled(enabled)

    def _after_connection_state_changed(self) -> None:
        self._update_calibration_controls()

    def _make_backend(self) -> tuple[object | None, str]:
        from galvo_gui.motion.mock import MockGalvoBackend

        index = self._mode_combo.currentIndex()
        if index == self.MODE_SIMULATED:
            return MockGalvoBackend(), "Simulated galvo"

        if index == self.MODE_GB511:
            try:
                from galvo_gui.motion.galvo_nea import GALVO_AVAILABLE, RealGalvoBackend
            except ImportError:
                GALVO_AVAILABLE = False
                RealGalvoBackend = None  # type: ignore[assignment, misc]
            if not GALVO_AVAILABLE:
                self.message.emit(
                    "galvo_functions not available. Using a simulated galvo instead."
                )
                return MockGalvoBackend(), "Simulated galvo (fallback)"
            try:
                return RealGalvoBackend(self._cal_edit.text()), "Galvo (GB511)"
            except Exception as exc:  # noqa: BLE001
                self.message.emit(f"Connection failed: {exc}")
                return None, ""

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
            self.message.emit(f"Connection failed: {exc}")
            return None, ""
        return backend, "Canon galvo"

    def _restore_settings(self) -> None:
        s = self._settings
        with contextlib.suppress(Exception):
            self._mode_combo.setCurrentIndex(int(s.value("mode_index", 0)))  # type: ignore[arg-type]
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
        apply_offset = s.value("apply_offset_correction", True)
        if isinstance(apply_offset, str):
            apply_offset = apply_offset.lower() != "false"
        self._offset_checkbox.setChecked(bool(apply_offset))
        self._update_calibration_controls()

    def save_settings(self) -> None:
        s = self._settings
        s.setValue("mode_index", self._mode_combo.currentIndex())
        s.setValue("cal_path", self._cal_edit.text())
        s.setValue("canon_serial_port", self._serial_port_edit.text())
        s.setValue("canon_board_index", self._board_index_edit.text())
        s.setValue("canon_program_file", self._program_file_edit.text())
        s.setValue("apply_offset_correction", self._offset_checkbox.isChecked())

    def _backend_supports_manual_calibration(self) -> bool:
        backend = self._backend
        if backend is None:
            return False
        if backend.__class__.__module__ == "galvo_gui.motion.mock":
            return False
        return callable(getattr(backend, "run_offset_calibration", None))

    def _update_calibration_controls(self) -> None:
        if not hasattr(self, "_offset_checkbox") or not hasattr(self, "_rerun_calibration_btn"):
            return
        connected = self.is_backend_connected()
        backend = self._backend
        setter = getattr(backend, "set_offset_correction_enabled", None)
        if connected and callable(setter):
            setter(self._offset_checkbox.isChecked())
        enabled = (
            connected
            and not self._op_busy
            and self._mode_combo.currentIndex() != self.MODE_SIMULATED
            and self._backend_supports_manual_calibration()
        )
        self._offset_checkbox.setEnabled(enabled)
        self._rerun_calibration_btn.setEnabled(enabled)
        if not self._op_busy:
            self._rerun_calibration_btn.setText("Run calibration at current position")

    def _on_offset_checkbox_toggled(self, checked: bool) -> None:
        backend = self._backend
        setter = getattr(backend, "set_offset_correction_enabled", None)
        if callable(setter):
            setter(bool(checked))
        self.save_settings()

    def _rerun_offset_calibration(self) -> None:
        backend = self._backend
        if backend is None or self._op_busy:
            return
        self._rerun_calibration_btn.setText("Calibrating…")
        self._connect_btn.setEnabled(False)
        self._start_op(
            backend.run_offset_calibration,  # type: ignore[attr-defined]
            self._on_rerun_offset_calibration_succeeded,
            self._on_rerun_offset_calibration_failed,
        )
        self._after_connection_state_changed()

    def _on_rerun_offset_calibration_succeeded(self) -> None:
        self._finish_op()
        result = self._take_runner_result()
        self._connect_btn.setEnabled(True)
        if isinstance(result, tuple) and len(result) == 2:
            self.message.emit(
                f"Offset calibration updated: X={float(result[0]):.0f}, Y={float(result[1]):.0f} pulses."
            )
        self._after_connection_state_changed()

    def _on_rerun_offset_calibration_failed(self, message: str) -> None:
        self._finish_op()
        self._connect_btn.setEnabled(True)
        self.message.emit(f"Calibration failed: {message}")
        self._after_connection_state_changed()


class ConnectionPanel(QWidget):
    """Connection tab: two independent connections (neaSNOM and galvomotor)."""

    nea_connected = pyqtSignal(object)      # NeaBackend
    nea_disconnected = pyqtSignal()
    galvo_connected = pyqtSignal(object)    # GalvoBackend
    galvo_disconnected = pyqtSignal()
    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        self._nea_section = NeaConnectionSection(self)
        self._galvo_section = GalvoConnectionSection(self)

        connections = QHBoxLayout()
        connections.setSpacing(12)
        connections.addWidget(self._nea_section)
        connections.addWidget(self._galvo_section)
        root.addLayout(connections)

        self._log = LogView(self)
        root.addWidget(self._log)
        root.addStretch()

        self._nea_section.connected.connect(self.nea_connected)
        self._nea_section.disconnected.connect(self.nea_disconnected)
        self._galvo_section.connected.connect(self.galvo_connected)
        self._galvo_section.disconnected.connect(self.galvo_disconnected)

        for section, tag in ((self._nea_section, "neaSNOM"), (self._galvo_section, "Galvo")):
            section.message.connect(
                lambda msg, t=tag: self._on_section_message(t, msg)
            )

    def _on_section_message(self, tag: str, message: str) -> None:
        self._log.append_line(f"{tag}: {message}")
        self.log_message.emit(f"{tag}: {message}")

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self._nea_section.shutdown()
        self._galvo_section.shutdown()
        super().closeEvent(event)  # type: ignore[misc]


class MotionPanel(QWidget):
    """Manual XY (galvo) and Z (neaSNOM) motion controls.

    The XY cluster is enabled only while a galvo backend is connected; the Z
    cluster only while a neaSNOM backend is connected.
    """

    log_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._galvo_backend: GalvoBackend | None = None
        self._nea_backend: NeaBackend | None = None
        self._locked = False
        self._xy_steps_available = False
        self._z_steps_available = False
        self._last_position_error: str | None = None
        self._settings = QSettings("galvo_gui", "MotionPanel")
        # Galvo state is kept in the hardware's native unit — encoder pulses.
        # nm only ever appears as a display conversion, never re-fed to a move.
        self._home_x_p = 0.0
        self._home_y_p = 0.0
        self._origin_x_p = 0.0
        self._origin_y_p = 0.0
        self._xy_units = "pulses"
        self._build_ui()
        self._restore_settings()

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(500)
        self._pos_timer.timeout.connect(self._refresh_position)

        self._apply_xy_enabled()
        self._apply_z_enabled()

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
        self._xy_step_label = QLabel("Step (pulses)")
        self._xy_step_label.setObjectName("MotionInlineLabel")
        header.addWidget(self._xy_step_label)
        self._xy_step_combo = self._build_step_combo(STANDARD_STEP_OPTIONS_PULSES, "100")
        header.addWidget(self._xy_step_combo)
        self._xy_units_combo = QComboBox()
        self._xy_units_combo.addItems(["pulses", "nm"])
        self._xy_units_combo.currentTextChanged.connect(self._set_xy_units)
        header.addWidget(self._xy_units_combo)
        self._menu_button = self._build_motion_menu_button()
        header.addWidget(self._menu_button)
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
        self._goto_x_edit = self._build_numeric_edit("0")
        self._goto_y_edit = self._build_numeric_edit("0")
        self._btn_go_xy = QPushButton("Go")
        self._btn_go_xy.clicked.connect(self._go_to_xy)
        self._btn_set_home = QPushButton("Set Home")
        self._btn_set_home.clicked.connect(self._set_home)

        readouts = QVBoxLayout()
        readouts.setSpacing(8)
        readouts.addWidget(self._build_readout_column("X live", self._x_label))
        readouts.addWidget(self._build_readout_column("Y live", self._y_label))
        readouts.addSpacing(2)
        readouts.addWidget(self._build_readout_column("Home", self._home_label))
        readouts.addWidget(self._build_goto_row())
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
            combo.addItem(f"{step_nm:g}", step_nm)
        self._set_combo_value(combo, float(default))
        return combo

    def _build_motion_readout(self, text: str, *, home: bool = False) -> ReadoutLabel:
        label = ReadoutLabel(text)
        label.setProperty("motionReadout", True)
        if home:
            label.setProperty("homeReadout", True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return label

    def _build_numeric_edit(self, text: str) -> QLineEdit:
        edit = QLineEdit(text)
        edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit.setFixedWidth(72)
        edit.setValidator(QDoubleValidator(edit))
        return edit

    def _build_goto_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Go To"))
        layout.addWidget(QLabel("X"))
        layout.addWidget(self._goto_x_edit)
        layout.addWidget(QLabel("Y"))
        layout.addWidget(self._goto_y_edit)
        layout.addWidget(self._btn_go_xy)
        layout.addStretch()
        return widget

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

    def _build_motion_menu_button(self) -> QToolButton:
        button = QToolButton()
        button.setText("...")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        self._set_origin_action = QAction("Set Origin", button)
        self._set_origin_action.triggered.connect(self._set_origin)
        menu.addAction(self._set_origin_action)
        button.setMenu(menu)
        return button

    # ------------------------------------------------------------------
    # Coordinate helpers (XY / galvo) — canonical unit is encoder pulses.
    # nm appears only as a display conversion (via pulses_per_nm) and is
    # never fed back into a move, so a command never round-trips through nm.
    # ------------------------------------------------------------------

    def _backend_home_xy_pulses(self) -> tuple[float, float]:
        return (self._origin_x_p + self._home_x_p, self._origin_y_p + self._home_y_p)

    def _pulses_per_nm(self) -> float:
        backend = self._galvo_backend
        if backend is None:
            return 1.0
        try:
            scale = float(backend.pulses_per_nm())
        except (TypeError, ValueError, AttributeError):
            return 1.0
        return scale if scale > 0 else 1.0

    def _xy_to_display(self, value_p: float) -> float:
        # Pulses are canonical; nm is a display-only division by K.
        if self._xy_units == "nm":
            return value_p / self._pulses_per_nm()
        return value_p

    def _xy_from_display(self, value: float) -> float:
        if self._xy_units == "nm":
            return value * self._pulses_per_nm()
        return value

    def _format_xy_value(self, value_p: float) -> str:
        return f"{self._xy_to_display(value_p):.0f}"

    def _set_combo_value(self, combo: QComboBox, value: float) -> None:
        for idx in range(combo.count()):
            if float(combo.itemData(idx)) == float(value):
                combo.setCurrentIndex(idx)
                return

    def _sync_xy_step_combo_labels(self) -> None:
        current_step_p = float(self._xy_step_combo.currentData())
        for idx in range(self._xy_step_combo.count()):
            step_p = float(self._xy_step_combo.itemData(idx))
            self._xy_step_combo.setItemText(idx, f"{self._xy_to_display(step_p):g}")
        self._set_combo_value(self._xy_step_combo, current_step_p)
        self._xy_step_label.setText(f"Step ({self._xy_units})")

    def _sync_xy_target_edits(self, x_p: float | None = None, y_p: float | None = None) -> None:
        x_p = 0.0 if x_p is None else x_p
        y_p = 0.0 if y_p is None else y_p
        self._goto_x_edit.setText(self._format_xy_value(x_p))
        self._goto_y_edit.setText(self._format_xy_value(y_p))

    def _set_xy_units(self, units: str) -> None:
        if units not in {"nm", "pulses"} or units == self._xy_units:
            return
        target_x_p = self._xy_from_display(float(self._goto_x_edit.text() or 0.0))
        target_y_p = self._xy_from_display(float(self._goto_y_edit.text() or 0.0))
        self._xy_units = units
        self._sync_xy_step_combo_labels()
        self._update_home_label()
        self._sync_xy_target_edits(target_x_p, target_y_p)
        self._refresh_position()
        self.save_settings()

    def _current_xy_from_origin_pulses(self) -> tuple[float, float]:
        if self._galvo_backend is None:
            return (self._home_x_p, self._home_y_p)
        x_rel_home_p, y_rel_home_p = self._galvo_backend.read_xy_pulses()
        return (x_rel_home_p + self._home_x_p, y_rel_home_p + self._home_y_p)

    def _apply_backend_home(self) -> None:
        if self._galvo_backend is None:
            return
        backend_home_x_p, backend_home_y_p = self._backend_home_xy_pulses()
        self._galvo_backend.set_home_pulses(backend_home_x_p, backend_home_y_p)

    # ------------------------------------------------------------------
    # Backend wiring
    # ------------------------------------------------------------------

    def set_galvo_backend(self, backend: GalvoBackend) -> None:
        self._galvo_backend = backend
        try:
            self._apply_backend_home()
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(f"Home restore error: {exc}")
        self._apply_xy_step_availability()
        self._refresh_position()
        self._maybe_start_timer()

    def clear_galvo_backend(self) -> None:
        self._galvo_backend = None
        self._x_label.setText("--")
        self._y_label.setText("--")
        self._xy_steps_available = False
        self._apply_xy_enabled()
        self._maybe_stop_timer()

    def set_nea_backend(self, backend: NeaBackend) -> None:
        self._nea_backend = backend
        self._apply_z_step_availability()
        self._refresh_position()
        self._maybe_start_timer()

    def clear_nea_backend(self) -> None:
        self._nea_backend = None
        self._z_label.setText("--")
        self._z_steps_available = False
        self._apply_z_enabled()
        self._maybe_stop_timer()

    def _maybe_start_timer(self) -> None:
        if self._locked:
            return
        if self._galvo_connected() or self._nea_connected():
            self._pos_timer.start()

    def _maybe_stop_timer(self) -> None:
        if not (self._galvo_connected() or self._nea_connected()):
            self._pos_timer.stop()

    def _galvo_connected(self) -> bool:
        return self._galvo_backend is not None and self._galvo_backend.is_connected()

    def _nea_connected(self) -> bool:
        return self._nea_backend is not None and self._nea_backend.is_connected()

    # ------------------------------------------------------------------
    # Motion commands
    # ------------------------------------------------------------------

    def _jog_xy(self, sign_x: int, sign_y: int) -> None:
        if self._galvo_backend is None:
            return
        step_p = float(self._xy_step_combo.currentData())
        before_x_p = before_y_p = 0.0
        try:
            before_x_p, before_y_p = self._current_xy_from_origin_pulses()
            self._galvo_backend.move_relative_pulses(sign_x * step_p, sign_y * step_p)
            after_x_p, after_y_p = self._current_xy_from_origin_pulses()
            self._log_xy_move(
                "jog",
                sign_x * step_p,
                sign_y * step_p,
                before_x_p,
                before_y_p,
                after_x_p,
                after_y_p,
            )
            self._refresh_position()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self._log_xy_move_error(
                "jog",
                sign_x * step_p,
                sign_y * step_p,
                before_x_p,
                before_y_p,
                str(exc),
            )
            self.log_message.emit(f"Move error: {exc}")

    def _jog_z(self, sign_z: int) -> None:
        if self._nea_backend is None:
            return
        step_nm = float(self._z_step_combo.currentText())
        try:
            self._nea_backend.move_z_relative(sign_z * step_nm)
            self._refresh_position()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.log_message.emit(f"Z move error: {exc}")

    def _goto_center(self) -> None:
        if self._galvo_backend is None:
            return
        before_x_p = before_y_p = 0.0
        try:
            before_x_p, before_y_p = self._current_xy_from_origin_pulses()
            self._galvo_backend.goto_center()
            after_x_p, after_y_p = self._current_xy_from_origin_pulses()
            self._log_xy_move(
                "center",
                after_x_p - before_x_p,
                after_y_p - before_y_p,
                before_x_p,
                before_y_p,
                after_x_p,
                after_y_p,
            )
            self._refresh_position()
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self._log_xy_move_error(
                "center",
                0.0,
                0.0,
                before_x_p,
                before_y_p,
                str(exc),
            )
            self.log_message.emit(f"Center error: {exc}")

    def _set_home(self) -> None:
        if self._galvo_backend is None:
            return
        try:
            self._home_x_p, self._home_y_p = self._current_xy_from_origin_pulses()
            self._galvo_backend.set_home_pulses(*self._backend_home_xy_pulses())
            self._update_home_label()
            self.save_settings()
            self._refresh_position()
            self.log_message.emit(
                f"Home set to ({self._home_x_p:.0f}, {self._home_y_p:.0f}) pulses"
            )
        except Exception as exc:  # noqa: BLE001 — DLL/SDK errors must reach the log
            self.log_message.emit(f"Set home error: {exc}")

    def _go_to_xy(self) -> None:
        if self._galvo_backend is None:
            return
        try:
            target_x_p = self._xy_from_display(float(self._goto_x_edit.text()))
            target_y_p = self._xy_from_display(float(self._goto_y_edit.text()))
        except ValueError:
            return
        current_x_p = current_y_p = 0.0
        dx_p = dy_p = 0.0
        try:
            current_x_p, current_y_p = self._current_xy_from_origin_pulses()
            dx_p = target_x_p - current_x_p
            dy_p = target_y_p - current_y_p
            self._galvo_backend.move_relative_pulses(dx_p, dy_p)
            after_x_p, after_y_p = self._current_xy_from_origin_pulses()
            self._log_xy_move(
                "goto",
                dx_p,
                dy_p,
                current_x_p,
                current_y_p,
                after_x_p,
                after_y_p,
            )
            self._refresh_position()
        except Exception as exc:  # noqa: BLE001
            self._log_xy_move_error(
                "goto",
                dx_p,
                dy_p,
                current_x_p,
                current_y_p,
                str(exc),
            )
            self.log_message.emit(f"Go to error: {exc}")

    def _set_origin(self) -> None:
        if self._galvo_backend is None:
            return
        try:
            current_x_p, current_y_p = self._current_xy_from_origin_pulses()
            self._origin_x_p += current_x_p
            self._origin_y_p += current_y_p
            self._home_x_p -= current_x_p
            self._home_y_p -= current_y_p
            self._apply_backend_home()
            self._update_home_label()
            self.save_settings()
            self._refresh_position()
            self.log_message.emit(
                f"Origin set to ({self._origin_x_p:.0f}, {self._origin_y_p:.0f}) pulses"
            )
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(f"Set origin error: {exc}")

    def _refresh_position(self) -> None:
        if self._galvo_connected():
            try:
                x_p, y_p = self._current_xy_from_origin_pulses()
            except Exception as exc:  # noqa: BLE001
                self._report_position_error(f"Position read error: {exc}")
            else:
                self._last_position_error = None
                self._x_label.setText(self._format_xy_value(x_p))
                self._y_label.setText(self._format_xy_value(y_p))
        if self._nea_connected():
            try:
                z_nm = self._nea_backend.read_z_nm()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                self._report_position_error(f"Z read error: {exc}")
            else:
                self._z_label.setText(f"{z_nm:.0f}")

    def _report_position_error(self, msg: str) -> None:
        # Report once per distinct failure instead of spamming the 500 ms
        # timer — but never fail silently: an invisible read error is how a
        # dead board masquerades as "connected but not moving".
        if msg != self._last_position_error:
            self._last_position_error = msg
            self.log_message.emit(msg)

    def _log_xy_move(
        self,
        kind: str,
        dx_p: float,
        dy_p: float,
        before_x_p: float,
        before_y_p: float,
        after_x_p: float,
        after_y_p: float,
    ) -> None:
        direction = self._format_xy_direction(dx_p, dy_p)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        diag = self._xy_move_diag_fields()
        _append_move_history(
            f"{stamp} kind={kind} status=ok step=({dx_p:.0f},{dy_p:.0f}) pulses "
            f"direction={direction} before=({before_x_p:.0f},{before_y_p:.0f}) "
            f"after=({after_x_p:.0f},{after_y_p:.0f}){diag}"
        )

    def _log_xy_move_error(
        self,
        kind: str,
        dx_p: float,
        dy_p: float,
        before_x_p: float,
        before_y_p: float,
        error: str,
    ) -> None:
        direction = self._format_xy_direction(dx_p, dy_p)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        diag = self._xy_move_diag_fields()
        _append_move_history(
            f"{stamp} kind={kind} status=error step=({dx_p:.0f},{dy_p:.0f}) pulses "
            f"direction={direction} before=({before_x_p:.0f},{before_y_p:.0f}) "
            f'{diag} error="{error}"'
        )

    def _xy_move_diag_fields(self) -> str:
        backend = self._galvo_backend
        if backend is None:
            return ""
        with contextlib.suppress(Exception):
            return _format_move_diag_fields(backend.last_move_diagnostics())
        return ""

    @staticmethod
    def _format_xy_direction(dx_p: float, dy_p: float) -> str:
        if dx_p > 0:
            return "right"
        if dx_p < 0:
            return "left"
        if dy_p > 0:
            return "up"
        if dy_p < 0:
            return "down"
        return "none"

    # ------------------------------------------------------------------
    # Step availability + enablement
    # ------------------------------------------------------------------

    def _apply_xy_step_availability(self) -> None:
        if not self._galvo_connected():
            self._xy_steps_available = False
            self._apply_xy_enabled()
            return
        xy_steps = self._galvo_backend.available_xy_steps_pulses()  # type: ignore[union-attr]
        self._set_combo_item_enabled(self._xy_step_combo, xy_steps)
        self._ensure_combo_selection(self._xy_step_combo, xy_steps)
        self._xy_steps_available = bool(xy_steps)
        self._apply_xy_enabled()

    def _apply_z_step_availability(self) -> None:
        if not self._nea_connected():
            self._z_steps_available = False
            self._apply_z_enabled()
            return
        z_steps = self._nea_backend.available_z_steps_nm()  # type: ignore[union-attr]
        self._set_combo_item_enabled(self._z_step_combo, z_steps)
        self._ensure_combo_selection(self._z_step_combo, z_steps)
        self._z_steps_available = bool(z_steps)
        self._apply_z_enabled()

    def _set_combo_item_enabled(
        self,
        combo: QComboBox,
        available_steps: tuple[float, ...],
    ) -> None:
        model = combo.model()
        item_fn = getattr(model, "item", None)
        available_steps_set = {float(step_nm) for step_nm in available_steps}
        for idx in range(combo.count()):
            item = item_fn(idx) if callable(item_fn) else None
            if item is not None:
                item.setEnabled(float(combo.itemData(idx)) in available_steps_set)

    def _ensure_combo_selection(
        self,
        combo: QComboBox,
        available_steps: tuple[float, ...],
    ) -> None:
        if not available_steps:
            return
        if float(combo.currentData()) in {float(step_nm) for step_nm in available_steps}:
            return
        self._set_combo_value(combo, float(available_steps[0]))

    def _apply_xy_enabled(self) -> None:
        enable_xy = self._xy_steps_available and not self._locked and self._galvo_connected()
        for btn in (
            self._btn_up,
            self._btn_down,
            self._btn_left,
            self._btn_right,
            self._btn_center,
            self._btn_go_xy,
            self._btn_set_home,
        ):
            btn.setEnabled(enable_xy)
        self._xy_step_combo.setEnabled(enable_xy)
        self._xy_units_combo.setEnabled(enable_xy)
        self._menu_button.setEnabled(enable_xy)
        self._goto_x_edit.setEnabled(enable_xy)
        self._goto_y_edit.setEnabled(enable_xy)

    def _apply_z_enabled(self) -> None:
        enable_z = self._z_steps_available and not self._locked and self._nea_connected()
        for btn in (self._btn_z_up, self._btn_z_down):
            btn.setEnabled(enable_z)
        self._z_step_combo.setEnabled(enable_z)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _restore_settings(self) -> None:
        xy_step = self._settings.value("xy_step_pulses", "100")
        z_step = self._settings.value("z_step_nm", "1000")
        xy_units = self._settings.value("xy_units", "pulses")
        with contextlib.suppress(Exception):
            self._set_combo_value(self._xy_step_combo, float(xy_step))
        if isinstance(z_step, str):
            self._z_step_combo.setCurrentText(z_step)
        if isinstance(xy_units, str) and xy_units in {"nm", "pulses"}:
            self._xy_units = xy_units
            self._xy_units_combo.setCurrentText(xy_units)
        with contextlib.suppress(Exception):
            self._home_x_p = float(self._settings.value("home_x_pulses", 0.0))
        with contextlib.suppress(Exception):
            self._home_y_p = float(self._settings.value("home_y_pulses", 0.0))
        with contextlib.suppress(Exception):
            self._origin_x_p = float(self._settings.value("origin_x_pulses", 0.0))
        with contextlib.suppress(Exception):
            self._origin_y_p = float(self._settings.value("origin_y_pulses", 0.0))
        self._sync_xy_step_combo_labels()
        self._sync_xy_target_edits()
        self._update_home_label()

    def save_settings(self) -> None:
        self._settings.setValue("xy_step_pulses", self._xy_step_combo.currentData())
        self._settings.setValue("z_step_nm", self._z_step_combo.currentText())
        self._settings.setValue("xy_units", self._xy_units)
        self._settings.setValue("home_x_pulses", self._home_x_p)
        self._settings.setValue("home_y_pulses", self._home_y_p)
        self._settings.setValue("origin_x_pulses", self._origin_x_p)
        self._settings.setValue("origin_y_pulses", self._origin_y_p)

    def _update_home_label(self) -> None:
        self._home_label.setText(
            f"{self._format_xy_value(self._home_x_p)}, {self._format_xy_value(self._home_y_p)}"
        )

    def closeEvent(self, event: object) -> None:  # type: ignore[override]
        self.save_settings()
        self._pos_timer.stop()
        super().closeEvent(event)  # type: ignore[misc]

    def lock_for_scan(self, locked: bool) -> None:
        self._locked = locked
        if locked:
            self._pos_timer.stop()
        else:
            self._maybe_start_timer()
        self._apply_xy_step_availability()
        self._apply_z_step_availability()


ManualPanel = ConnectionPanel
