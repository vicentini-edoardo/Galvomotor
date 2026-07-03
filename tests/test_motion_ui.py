"""UI tests for the split connection/motion tab layout."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QLabel

from galvo_gui.gui.main_window import MainWindow
from galvo_gui.gui.panel_manual import ConnectionPanel, MotionPanel


@pytest.fixture
def qapp():  # type: ignore[no-untyped-def]
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(autouse=True)
def _clear_qsettings() -> None:
    for group in ("MotionPanel", "GalvoConnection", "NeaConnection"):
        QSettings("galvo_gui", group).clear()


def _connect_mocks(panel: MotionPanel):
    """Attach connected mock galvo + neaSNOM backends to a MotionPanel."""
    from galvo_gui.motion.mock import MockGalvoBackend, MockNeaBackend

    galvo = MockGalvoBackend()
    nea = MockNeaBackend()
    galvo.connect()
    nea.connect()
    panel.set_galvo_backend(galvo)
    panel.set_nea_backend(nea)
    return galvo, nea


def test_main_window_has_connection_motion_and_scan_tabs(qapp: object) -> None:
    win = MainWindow()

    assert [win._tabs.tabText(i) for i in range(win._tabs.count())] == [
        "Connection",
        "Motion",
        "Scan",
    ]


def test_motion_panel_uses_step_combos_and_has_z_controls(qapp: object) -> None:
    panel = MotionPanel()

    assert not panel._xy_step_combo.isEditable()
    assert not panel._z_step_combo.isEditable()
    assert any(label.text() == "Galvomotor XY" for label in panel.findChildren(QLabel))
    assert any(label.text() == "neaSNOM Z" for label in panel.findChildren(QLabel))
    assert [panel._xy_step_combo.itemText(i) for i in range(panel._xy_step_combo.count())] == [
        "0.1",
        "50",
        "100",
        "500",
        "1000",
    ]
    assert [panel._z_step_combo.itemText(i) for i in range(panel._z_step_combo.count())] == [
        "10",
        "100",
        "1000",
        "10000",
    ]
    assert panel._btn_z_up.text() == "▲"
    assert panel._btn_z_down.text() == "▼"
    assert panel._btn_set_home.text() == "Set Home"
    assert panel._btn_go_xy.text() == "Go"
    assert not panel._xy_step_combo.isEnabled()
    assert not panel._z_step_combo.isEnabled()


def test_xy_controls_need_galvo_and_z_controls_need_nea(qapp: object) -> None:
    from galvo_gui.motion.mock import MockGalvoBackend, MockNeaBackend

    panel = MotionPanel()
    # Only the galvo connected: XY live, Z still disabled.
    galvo = MockGalvoBackend()
    galvo.connect()
    panel.set_galvo_backend(galvo)
    assert panel._btn_up.isEnabled()
    assert not panel._btn_z_up.isEnabled()

    # neaSNOM connected too: Z becomes live.
    nea = MockNeaBackend()
    nea.connect()
    panel.set_nea_backend(nea)
    assert panel._btn_z_up.isEnabled()

    # Dropping the galvo disables XY but leaves Z alone.
    panel.clear_galvo_backend()
    assert not panel._btn_up.isEnabled()
    assert panel._btn_z_up.isEnabled()


def test_motion_panel_locks_and_unlocks_with_backend(qapp: object) -> None:
    panel = MotionPanel()
    _connect_mocks(panel)

    assert panel._xy_step_combo.isEnabled()
    assert panel._btn_up.isEnabled()
    assert panel._btn_z_up.isEnabled()

    panel.lock_for_scan(True)
    assert not panel._xy_step_combo.isEnabled()
    assert not panel._btn_up.isEnabled()
    assert not panel._btn_z_up.isEnabled()

    panel.lock_for_scan(False)
    assert panel._xy_step_combo.isEnabled()
    assert panel._btn_up.isEnabled()
    assert panel._btn_z_up.isEnabled()


def test_motion_panel_persists_selected_steps_and_home(qapp: object) -> None:
    from galvo_gui.motion.mock import MockGalvoBackend

    panel = MotionPanel()
    panel._settings.clear()
    panel._xy_step_combo.setCurrentText("50")
    panel._z_step_combo.setCurrentText("10000")
    backend = MockGalvoBackend()
    backend.connect()
    backend.move_relative(250.0, -125.0)
    panel.set_galvo_backend(backend)
    panel._set_home()
    panel.save_settings()

    restored = MotionPanel()

    assert restored._xy_step_combo.currentText() == "50"
    assert restored._z_step_combo.currentText() == "10000"
    assert restored._home_x_nm == 250.0
    assert restored._home_y_nm == -125.0
    assert restored._home_label.text() == "250, -125"


def test_motion_panel_go_to_fields_move_to_manual_values(qapp: object) -> None:
    from galvo_gui.motion.mock import MockGalvoBackend

    panel = MotionPanel()
    backend = MockGalvoBackend()
    backend.connect()
    backend.move_relative(300.0, -200.0)
    panel.set_galvo_backend(backend)

    panel._goto_x_edit.setText("100")
    panel._goto_y_edit.setText("-50")
    panel._go_to_xy()
    qapp.processEvents()

    assert backend.read_xy_nm() == (100.0, -50.0)
    assert panel._x_label.text() == "100"
    assert panel._y_label.text() == "-50"


def test_motion_panel_can_switch_xy_units_to_pulses(qapp: object) -> None:
    from galvo_gui.motion.mock import MockGalvoBackend

    panel = MotionPanel()
    backend = MockGalvoBackend()
    backend.connect()
    backend.move_relative(300.0, -200.0)
    panel.set_galvo_backend(backend)

    panel._xy_units_combo.setCurrentText("pulses")
    qapp.processEvents()

    assert panel._x_label.text() == "300"
    assert panel._y_label.text() == "-200"
    assert panel._goto_x_edit.text() == "0"
    assert panel._goto_y_edit.text() == "0"

    panel._goto_x_edit.setText("100")
    panel._goto_y_edit.setText("-50")
    panel._go_to_xy()

    assert backend.read_xy_nm() == (100.0, -50.0)
    assert panel._x_label.text() == "100"
    assert panel._y_label.text() == "-50"


def test_motion_panel_set_origin_references_home_from_new_origin(qapp: object) -> None:
    from galvo_gui.motion.mock import MockGalvoBackend

    panel = MotionPanel()
    panel._settings.clear()
    backend = MockGalvoBackend()
    backend.connect()
    backend.move_relative(300.0, -200.0)
    panel.set_galvo_backend(backend)

    panel._home_x_nm = 100.0
    panel._home_y_nm = -50.0
    panel._apply_backend_home()
    panel._update_home_label()

    panel._set_origin()

    assert panel._origin_x_nm == 300.0
    assert panel._origin_y_nm == -200.0
    assert panel._home_x_nm == -200.0
    assert panel._home_y_nm == 150.0
    assert panel._x_label.text() == "0"
    assert panel._y_label.text() == "0"

    panel._goto_center()

    assert backend.read_xy_nm() == (0.0, 0.0)
    assert panel._x_label.text() == "-200"
    assert panel._y_label.text() == "150"


def test_motion_panel_restores_saved_home_on_connect(qapp: object) -> None:
    panel = MotionPanel()
    panel._settings.clear()
    panel._home_x_nm = 300.0
    panel._home_y_nm = -200.0
    panel.save_settings()

    restored = MotionPanel()
    from galvo_gui.motion.mock import MockGalvoBackend

    backend = MockGalvoBackend()
    backend.connect()
    backend.move_relative(300.0, -200.0)
    restored.set_galvo_backend(backend)

    assert backend.read_xy_nm() == (0.0, 0.0)


# ----------------------------------------------------------------------
# Connection panel
# ----------------------------------------------------------------------


def test_connection_panel_has_separate_nea_and_galvo_sections(qapp: object) -> None:
    panel = ConnectionPanel()

    assert "neaSNOM" in panel._nea_section.title()
    assert "Galvo" in panel._galvo_section.title() or "galvo" in panel._galvo_section.title()
    # neaSNOM has no driver-mode selector; galvo carries it.
    assert not hasattr(panel._nea_section, "_mode_combo")
    assert [
        panel._galvo_section._mode_combo.itemText(i)
        for i in range(panel._galvo_section._mode_combo.count())
    ] == [
        "Simulated galvo (no hardware)",
        "GB511 board (galvo_functions)",
        "Canon GC-211/212 (GB511 + RS-232 high-speed)",
    ]


def test_galvo_section_shows_canon_fields_for_canon_mode(qapp: object) -> None:
    panel = ConnectionPanel()
    panel.show()
    galvo = panel._galvo_section
    galvo._mode_combo.setCurrentIndex(galvo.MODE_CANON)

    assert galvo._serial_port_edit.isVisible()
    assert galvo._board_index_edit.isVisible()
    assert galvo._program_file_edit.isVisible()
    assert galvo._cal_edit.isVisible()


def _process_until(qapp, predicate, timeout_s: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for GUI state"
        qapp.processEvents()
        time.sleep(0.01)


def test_connect_and_disconnect_run_off_the_gui_thread(qapp) -> None:
    """A slow backend.connect must not freeze the section: the button switches
    to a progress label immediately and the connection completes via the worker."""
    import time

    from galvo_gui.motion.mock import MockGalvoBackend

    original_connect = MockGalvoBackend.connect

    def slow_connect(self, host=""):
        time.sleep(0.2)
        original_connect(self, host)

    MockGalvoBackend.connect = slow_connect
    try:
        panel = ConnectionPanel()
        section = panel._galvo_section
        section._mode_combo.setCurrentIndex(section.MODE_SIMULATED)
        section._on_connect_toggle()

        # Immediately after the click the GUI is free and shows progress.
        assert section._connect_btn.text() == "Connecting…"
        assert not section._connect_btn.isEnabled()
        assert section._backend is None

        _process_until(qapp, lambda: section._backend is not None)
        assert section._connect_btn.text() == "Disconnect"
        assert section._connect_btn.isEnabled()
        assert not section._mode_combo.isEnabled()
    finally:
        MockGalvoBackend.connect = original_connect

    section._on_connect_toggle()
    assert section._connect_btn.text() == "Disconnecting…"
    assert section._backend is None  # detached before hardware teardown

    _process_until(qapp, lambda: section._connect_btn.text() == "Connect")
    assert section._connect_btn.isEnabled()
    assert section._mode_combo.isEnabled()


def test_reconnect_after_disconnect_completes_on_the_same_worker_thread(qapp, monkeypatch) -> None:
    """Regression: after a disconnect the worker thread was torn down, so the
    next connect ran on a fresh thread. The thread-affine SDK then hung until
    the app was restarted. Connect, disconnect, and reconnect must all run on
    one persistent worker thread."""
    import threading

    from galvo_gui.motion.mock import MockGalvoBackend

    op_thread_idents: list[int] = []
    original_connect = MockGalvoBackend.connect
    original_disconnect = MockGalvoBackend.disconnect

    def recording_connect(self, host=""):
        op_thread_idents.append(threading.get_ident())
        original_connect(self, host)

    def recording_disconnect(self):
        op_thread_idents.append(threading.get_ident())
        original_disconnect(self)

    monkeypatch.setattr(MockGalvoBackend, "connect", recording_connect)
    monkeypatch.setattr(MockGalvoBackend, "disconnect", recording_disconnect)

    panel = ConnectionPanel()
    section = panel._galvo_section
    section._mode_combo.setCurrentIndex(section.MODE_SIMULATED)

    section._on_connect_toggle()  # first connect
    _process_until(qapp, lambda: section._backend is not None)
    first_thread = section._op_thread
    assert first_thread is not None and first_thread.isRunning()

    section._on_connect_toggle()  # disconnect
    _process_until(
        qapp,
        lambda: section._connect_btn.text() == "Connect" and section._connect_btn.isEnabled(),
    )
    assert section._op_thread is first_thread  # thread survives the disconnect

    section._on_connect_toggle()  # reconnect — this used to hang on hardware
    _process_until(qapp, lambda: section._backend is not None)

    assert section._backend.is_connected()
    assert section._connect_btn.text() == "Disconnect"
    assert section._op_thread is first_thread
    assert op_thread_idents and len(set(op_thread_idents)) == 1
    assert op_thread_idents[0] != threading.get_ident()  # and off the GUI thread

    panel.close()


def test_close_while_connected_disconnects_on_the_worker_thread(qapp) -> None:
    """Closing the app with a live session must tear it down from the op
    thread (SDK thread-affinity), not from the GUI thread, and must stop the
    worker thread so the process can exit cleanly."""
    import threading

    from galvo_gui.motion.mock import MockGalvoBackend

    disconnect_idents: list[int] = []
    original_disconnect = MockGalvoBackend.disconnect

    def recording_disconnect(self):
        disconnect_idents.append(threading.get_ident())
        original_disconnect(self)

    MockGalvoBackend.disconnect = recording_disconnect
    try:
        panel = ConnectionPanel()
        section = panel._galvo_section
        section._mode_combo.setCurrentIndex(section.MODE_SIMULATED)
        section._on_connect_toggle()
        _process_until(qapp, lambda: section._backend is not None)
        backend = section._backend
        op_thread = section._op_thread

        panel.close()
    finally:
        MockGalvoBackend.disconnect = original_disconnect

    assert not backend.is_connected()
    assert disconnect_idents and disconnect_idents[0] != threading.get_ident()
    assert section._op_thread is None
    assert not op_thread.isRunning()


def test_connect_failure_reenables_the_section(qapp) -> None:
    from galvo_gui.motion.mock import MockGalvoBackend

    original_connect = MockGalvoBackend.connect

    def failing_connect(self, host=""):
        raise RuntimeError("server unreachable")

    MockGalvoBackend.connect = failing_connect
    try:
        panel = ConnectionPanel()
        section = panel._galvo_section
        section._mode_combo.setCurrentIndex(section.MODE_SIMULATED)
        section._on_connect_toggle()
        _process_until(qapp, lambda: section._connect_btn.text() == "Connect")
    finally:
        MockGalvoBackend.connect = original_connect

    assert section._backend is None
    assert section._connect_btn.isEnabled()
    assert section._mode_combo.isEnabled()
    assert "Connection failed: server unreachable" in panel._log.toPlainText()


def test_galvo_section_persists_canon_settings(qapp: object) -> None:
    panel = ConnectionPanel()
    section = panel._galvo_section
    section._settings.clear()
    section._mode_combo.setCurrentIndex(section.MODE_CANON)
    section._serial_port_edit.setText("COM8")
    section._board_index_edit.setText("3")
    section._program_file_edit.setText(r"C:\Canon\gb511_core0.hex")
    section.save_settings()

    restored = ConnectionPanel()._galvo_section

    assert restored._mode_combo.currentIndex() == section.MODE_CANON
    assert restored._serial_port_edit.text() == "COM8"
    assert restored._board_index_edit.text() == "3"
    assert restored._program_file_edit.text() == r"C:\Canon\gb511_core0.hex"


def test_nea_section_persists_host(qapp: object) -> None:
    panel = ConnectionPanel()
    section = panel._nea_section
    section._settings.clear()
    section._host_edit.setText("nea-box.local")
    section.save_settings()

    restored = ConnectionPanel()._nea_section
    assert restored._host_edit.text() == "nea-box.local"


def test_galvo_section_passes_canon_settings_to_canon_backend(qapp, monkeypatch) -> None:
    import galvo_gui.motion.canon.backend as canon_backend_module

    captured = {}

    class FakeCanonBackend:
        def __init__(
            self,
            cal_files_path="",
            *,
            board_index=0,
            program_file=None,
            serial_port=None,
        ) -> None:
            captured["init"] = {
                "cal_files_path": cal_files_path,
                "board_index": board_index,
                "program_file": program_file,
                "serial_port": serial_port,
            }

        def connect(self, host="") -> None:
            captured["connect_host"] = host

        def disconnect(self) -> None:
            return None

        def is_connected(self) -> bool:
            return True

    monkeypatch.setattr(canon_backend_module, "CanonGalvoBackend", FakeCanonBackend)

    panel = ConnectionPanel()
    section = panel._galvo_section
    section._mode_combo.setCurrentIndex(section.MODE_CANON)
    section._cal_edit.setText("/tmp/cal_files")
    section._serial_port_edit.setText("COM8")
    section._board_index_edit.setText("3")
    section._program_file_edit.setText(r"C:\Canon\gb511_core0.hex")
    section._connect()
    _process_until(qapp, lambda: section._backend is not None)

    assert captured["init"] == {
        "cal_files_path": "/tmp/cal_files",
        "board_index": 3,
        "program_file": r"C:\Canon\gb511_core0.hex",
        "serial_port": "COM8",
    }
    # The galvo connection does not address a neaSNOM host.
    assert captured["connect_host"] == ""


def test_galvo_section_does_not_force_invalid_canon_board_index(qapp, monkeypatch) -> None:
    import galvo_gui.motion.canon.backend as canon_backend_module

    captured = {}

    class FakeCanonBackend:
        def __init__(
            self,
            cal_files_path="",
            *,
            board_index=None,
            program_file=None,
            serial_port=None,
        ) -> None:
            captured["board_index"] = board_index

        def connect(self, host="") -> None:
            return None

        def disconnect(self) -> None:
            return None

        def is_connected(self) -> bool:
            return True

    monkeypatch.setattr(canon_backend_module, "CanonGalvoBackend", FakeCanonBackend)

    panel = ConnectionPanel()
    section = panel._galvo_section
    section._mode_combo.setCurrentIndex(section.MODE_CANON)
    section._board_index_edit.setText("0")
    section._connect()
    _process_until(qapp, lambda: section._backend is not None)

    assert captured["board_index"] is None
