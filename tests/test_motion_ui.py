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
        "0.1",
        "1",
        "10",
        "100",
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
    panel._z_step_combo.setCurrentText("1")
    panel.save_settings()

    restored = MotionPanel()

    assert restored._xy_step_combo.currentText() == "10"
    assert restored._z_step_combo.currentText() == "1"
