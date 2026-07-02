"""Tests for Windows application icon configuration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from galvo_gui import __main__ as app_main


@pytest.fixture
def qapp():  # type: ignore[no-untyped-def]
    app = QApplication.instance() or QApplication([])
    return app


def test_load_app_icon_uses_repo_icon(qapp: object) -> None:
    icon = app_main._load_app_icon()

    assert Path(__file__).resolve().parents[1] / "icon.ico" == app_main._ICON_PATH
    assert app_main._ICON_PATH.exists()
    assert not icon.isNull()


def test_configure_application_sets_window_icon(qapp: QApplication) -> None:
    qapp.setWindowIcon(QIcon())

    app_main._configure_application(qapp)

    assert not qapp.windowIcon().isNull()


def test_set_windows_app_id_calls_shell32_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    shell32 = SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=lambda app_id: calls.append(app_id)
    )

    monkeypatch.setattr(app_main.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(
        app_main,
        "ctypes",
        SimpleNamespace(windll=SimpleNamespace(shell32=shell32)),
    )

    app_main._set_windows_app_id()

    assert calls == [app_main._WINDOWS_APP_ID]
