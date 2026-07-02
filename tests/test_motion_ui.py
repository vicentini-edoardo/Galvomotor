"""UI tests for the split connection/motion tab layout."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from galvo_gui.gui.main_window import MainWindow
from galvo_gui.gui.panel_manual import MotionPanel


@pytest.fixture
def qapp():  # type: ignore[no-untyped-def]
    app = QApplication.instance() or QApplication([])
    return app


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
    assert [panel._xy_step_combo.itemText(i) for i in range(panel._xy_step_combo.count())] == [
        "0.1",
        "1",
        "10",
        "100",
    ]
    assert [panel._z_step_combo.itemText(i) for i in range(panel._z_step_combo.count())] == [
        "10",
        "100",
        "1000",
        "10000",
    ]
    assert panel._btn_z_up.text() == "▲"
    assert panel._btn_z_down.text() == "▼"
    assert not panel._xy_step_combo.isEnabled()
    assert not panel._z_step_combo.isEnabled()


def test_motion_panel_locks_and_unlocks_with_backend(qapp: object) -> None:
    from galvo_gui.motion.mock import MockGalvoBackend

    backend = MockGalvoBackend()
    backend.connect()

    panel = MotionPanel()
    panel.set_backend(backend)

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


def test_motion_panel_persists_selected_steps(qapp: object) -> None:
    panel = MotionPanel()
    panel._settings.clear()
    panel._xy_step_combo.setCurrentText("10")
    panel._z_step_combo.setCurrentText("10000")
    panel.save_settings()

    restored = MotionPanel()

    assert restored._xy_step_combo.currentText() == "10"
    assert restored._z_step_combo.currentText() == "10000"


def test_connection_panel_shows_canon_fields_only_for_canon_backend(qapp: object) -> None:
    from galvo_gui.gui.panel_manual import ConnectionPanel

    panel = ConnectionPanel()
    panel.show()
    panel._backend_combo.setCurrentIndex(2)

    assert panel._serial_port_edit.isVisible()
    assert panel._board_index_edit.isVisible()
    assert panel._program_file_edit.isVisible()
    assert not panel._host_edit.isVisible()
    assert not panel._cal_edit.isVisible()


def _process_until(qapp, predicate, timeout_s: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for GUI state"
        qapp.processEvents()
        time.sleep(0.01)


def test_connect_and_disconnect_run_off_the_gui_thread(qapp) -> None:
    """A slow backend.connect must not freeze the panel: the button switches to
    a progress label immediately and the connection completes via the worker."""
    import time

    from galvo_gui.gui.panel_manual import ConnectionPanel
    from galvo_gui.motion.mock import MockGalvoBackend

    original_connect = MockGalvoBackend.connect

    def slow_connect(self, host=""):
        time.sleep(0.2)
        original_connect(self, host)

    MockGalvoBackend.connect = slow_connect
    try:
        panel = ConnectionPanel()
        panel._backend_combo.setCurrentIndex(0)
        panel._on_connect_toggle()

        # Immediately after the click the GUI is free and shows progress.
        assert panel._connect_btn.text() == "Connecting…"
        assert not panel._connect_btn.isEnabled()
        assert panel._backend is None

        _process_until(qapp, lambda: panel._backend is not None)
        assert panel._connect_btn.text() == "Disconnect"
        assert panel._connect_btn.isEnabled()
        assert not panel._backend_combo.isEnabled()
    finally:
        MockGalvoBackend.connect = original_connect

    panel._on_connect_toggle()
    assert panel._connect_btn.text() == "Disconnecting…"
    assert panel._backend is None  # detached before hardware teardown

    _process_until(qapp, lambda: panel._connect_btn.text() == "Connect")
    assert panel._connect_btn.isEnabled()
    assert panel._backend_combo.isEnabled()


def test_connect_failure_reenables_the_panel(qapp) -> None:
    from galvo_gui.gui.panel_manual import ConnectionPanel
    from galvo_gui.motion.mock import MockGalvoBackend

    original_connect = MockGalvoBackend.connect

    def failing_connect(self, host=""):
        raise RuntimeError("server unreachable")

    MockGalvoBackend.connect = failing_connect
    try:
        panel = ConnectionPanel()
        panel._backend_combo.setCurrentIndex(0)
        panel._on_connect_toggle()
        _process_until(qapp, lambda: panel._connect_btn.text() == "Connect")
    finally:
        MockGalvoBackend.connect = original_connect

    assert panel._backend is None
    assert panel._connect_btn.isEnabled()
    assert panel._backend_combo.isEnabled()
    assert "Connection failed: server unreachable" in panel._log.toPlainText()


def test_connection_panel_persists_canon_settings(qapp: object) -> None:
    from galvo_gui.gui.panel_manual import ConnectionPanel

    panel = ConnectionPanel()
    panel._settings.clear()
    panel._backend_combo.setCurrentIndex(2)
    panel._serial_port_edit.setText("COM8")
    panel._board_index_edit.setText("3")
    panel._program_file_edit.setText(r"C:\Canon\gb511_core0.hex")
    panel.save_settings()

    restored = ConnectionPanel()

    assert restored._backend_combo.currentIndex() == 2
    assert restored._serial_port_edit.text() == "COM8"
    assert restored._board_index_edit.text() == "3"
    assert restored._program_file_edit.text() == r"C:\Canon\gb511_core0.hex"
